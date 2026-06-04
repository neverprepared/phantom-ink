package main

import (
	"bytes"
	"context"
	"fmt"
	"math"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"phantom-ink/brainbox"
)

// localRunner is an in-process runner that handles session work items by
// opening iTerm2 tabs (session.create) or running one-shot claude invocations
// (session.query). It registers with the remote brainbox API and long-polls
// for pending work items.
type localRunner struct {
	client    *brainbox.Client
	name      string
	machineID string

	longPoll *http.Client

	stopCh  chan struct{}
	stopped chan struct{}

	procs sync.Map // sessionName → *exec.Cmd (for session.stop/delete)
}

func newLocalRunner(client *brainbox.Client, name, machineID string) *localRunner {
	return &localRunner{
		client:    client,
		name:      name,
		machineID: machineID,
		longPoll:  brainbox.LongPollHTTPClient(),
		stopCh:    make(chan struct{}),
		stopped:   make(chan struct{}),
	}
}

// Start begins the registration + poll loop in a background goroutine.
func (r *localRunner) Start(ctx context.Context) {
	go r.run(ctx)
}

// Stop signals the runner to exit and waits for it to finish.
func (r *localRunner) Stop() {
	close(r.stopCh)
	<-r.stopped
}

// Wait blocks until the runner has stopped.
func (r *localRunner) Wait() {
	<-r.stopped
}

func (r *localRunner) run(ctx context.Context) {
	defer close(r.stopped)

	if !r.registerWithBackoff(ctx) {
		return
	}

	heartbeat := time.NewTicker(30 * time.Second)
	defer heartbeat.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-r.stopCh:
			return
		case <-heartbeat.C:
			if err := r.client.PostRunnerHeartbeat(r.name); err != nil {
				fmt.Printf("local runner heartbeat: %v\n", err)
			}
		default:
		}

		select {
		case <-ctx.Done():
			return
		case <-r.stopCh:
			return
		default:
		}

		item, err := r.client.GetPendingWork(r.name, r.longPoll)
		if err != nil {
			select {
			case <-ctx.Done():
				return
			case <-r.stopCh:
				return
			case <-time.After(2 * time.Second):
			}
			continue
		}
		if item != nil {
			go r.handleWork(ctx, item)
		}
	}
}

func (r *localRunner) registerWithBackoff(ctx context.Context) bool {
	req := brainbox.RegisterRunnerRequest{
		Name: r.name,
		// "docker" is the capability key the API validates against the backend
		// field — it means "can handle docker-backend session.create work items".
		// The actual execution is a local process, not a container.
		Capabilities: map[string]bool{
			"docker": true,
		},
		MachineID:     r.machineID,
		MaxConcurrent: 4,
	}
	delay := 5 * time.Second
	const maxDelay = 30 * time.Second
	for attempt := 0; ; attempt++ {
		if err := r.client.RegisterRunner(req); err == nil {
			return true
		} else {
			fmt.Printf("local runner register (attempt %d): %v\n", attempt+1, err)
		}
		backoff := time.Duration(math.Min(float64(delay)*math.Pow(2, float64(attempt)), float64(maxDelay)))
		select {
		case <-ctx.Done():
			return false
		case <-r.stopCh:
			return false
		case <-time.After(backoff):
		}
	}
}

func (r *localRunner) handleWork(ctx context.Context, item *brainbox.RunnerWorkItem) {
	var result brainbox.RunnerResult

	switch item.Kind {
	case "session.create":
		result = r.handleSessionCreate(item)
	case "session.query":
		result = r.handleSessionQuery(ctx, item)
	case "session.stop", "session.delete":
		result = r.handleSessionStop(item)
	default:
		result = brainbox.RunnerResult{OK: false, Error: fmt.Sprintf("unsupported kind: %s", item.Kind)}
	}

	if err := r.client.PostRunnerResult(r.name, item.ID, result); err != nil {
		fmt.Printf("local runner post result (work %s): %v\n", item.ID, err)
	}
}

// handleSessionCreate opens an iTerm2 tab with `claude` running in the
// session's working directory and immediately returns a SessionContext so the
// API endpoint unblocks. The process runs interactively in the terminal.
func (r *localRunner) handleSessionCreate(item *brainbox.RunnerWorkItem) brainbox.RunnerResult {
	sessionName, _ := item.Payload["session_name"].(string)
	if sessionName == "" {
		sessionName, _ = item.Payload["name"].(string)
	}
	workDir, _ := item.Payload["workspace_home"].(string)
	if workDir == "" {
		workDir = os.Getenv("HOME")
	}
	role, _ := item.Payload["role"].(string)
	if role == "" {
		role = "developer"
	}
	ttl := 3600
	if v, ok := item.Payload["ttl"].(float64); ok && v > 0 {
		ttl = int(v)
	}
	workspaceProfile, _ := item.Payload["workspace_profile"].(string)

	// Open an iTerm2 tab so the user can interact with the session.
	if err := openLocalSessionTab(workDir); err != nil {
		fmt.Printf("local runner open terminal: %v\n", err)
		// Non-fatal — session is still registered even if terminal open fails.
	}

	// Return a SessionContext that satisfies the API's Pydantic model.
	// backend must be "docker" (literal in the model), port=0 signals no ttyd.
	return brainbox.RunnerResult{
		OK: true,
		Data: map[string]any{
			"session_name":      sessionName,
			"container_name":    sessionName,
			"port":              0,
			"role":              role,
			"state":             "running",
			"backend":           "docker",
			"created_at":        time.Now().UnixMilli(),
			"ttl":               ttl,
			"hardened":          false,
			"runner_name":       r.name,
			"runner_host":       "",
			"workspace_profile": workspaceProfile,
			"workspace_home":    workDir,
			"delivery":          "image",
		},
	}
}

// handleSessionQuery runs a one-shot `claude -p` invocation and returns the output.
// Used by playbook and chain steps.
func (r *localRunner) handleSessionQuery(ctx context.Context, item *brainbox.RunnerWorkItem) brainbox.RunnerResult {
	prompt, _ := item.Payload["prompt"].(string)
	if prompt == "" {
		prompt, _ = item.Payload["task_description"].(string)
	}
	if prompt == "" {
		return brainbox.RunnerResult{OK: false, Error: "missing prompt"}
	}

	workDir, _ := item.Payload["workspace_home"].(string)
	if workDir == "" {
		workDir = os.Getenv("HOME")
	}

	ttl := 600 * time.Second
	if v, ok := item.Payload["ttl"].(float64); ok && v > 0 {
		ttl = time.Duration(v) * time.Second
	}

	execCtx, cancel := context.WithTimeout(ctx, ttl)
	defer cancel()

	sessionName, _ := item.Payload["session_name"].(string)

	cmd := exec.CommandContext(execCtx, "claude", "--dangerously-skip-permissions", "-p", prompt)
	cmd.Dir = workDir

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if sessionName != "" {
		r.procs.Store(sessionName, cmd)
		defer r.procs.Delete(sessionName)
	}

	if err := cmd.Run(); err != nil {
		errMsg := strings.TrimSpace(stderr.String())
		if errMsg == "" {
			errMsg = err.Error()
		}
		return brainbox.RunnerResult{OK: false, Error: errMsg}
	}

	return brainbox.RunnerResult{
		OK:   true,
		Data: map[string]any{"output": strings.TrimSpace(stdout.String())},
	}
}

func (r *localRunner) handleSessionStop(item *brainbox.RunnerWorkItem) brainbox.RunnerResult {
	sessionName, _ := item.Payload["session_name"].(string)
	if sessionName != "" {
		if v, ok := r.procs.Load(sessionName); ok {
			if cmd, ok := v.(*exec.Cmd); ok && cmd.Process != nil {
				_ = cmd.Process.Kill()
			}
			r.procs.Delete(sessionName)
		}
	}
	return brainbox.RunnerResult{OK: true}
}

// openLocalSessionTab opens a new terminal tab in iTerm2 (or Terminal.app as
// fallback) running `claude --dangerously-skip-permissions` in workDir.
func openLocalSessionTab(workDir string) error {
	for _, c := range workDir {
		if c == '"' || c == '\\' || c < 0x20 || c > 0x7e {
			return fmt.Errorf("invalid character in workDir")
		}
	}
	if workDir == "" {
		workDir = "~"
	}

	itermScript := fmt.Sprintf(`
tell application "System Events"
	if exists (process "iTerm2") then
		tell application "iTerm2"
			activate
			tell current window
				create tab with default profile
				tell current session
					write text "cd \"%s\" && claude --dangerously-skip-permissions"
				end tell
			end tell
		end tell
		return "ok"
	end if
end tell
return "not_found"`, workDir)

	out, err := exec.Command("osascript", "-e", itermScript).Output()
	if err == nil && strings.TrimSpace(string(out)) == "ok" {
		return nil
	}

	termScript := fmt.Sprintf(`
tell application "System Events"
	if exists (process "Terminal") then
		tell application "Terminal"
			activate
			do script "cd \"%s\" && claude --dangerously-skip-permissions"
		end tell
		return "ok"
	end if
end tell
return "not_found"`, workDir)

	out, err = exec.Command("osascript", "-e", termScript).Output()
	if err == nil && strings.TrimSpace(string(out)) == "ok" {
		return nil
	}

	return fmt.Errorf("no iTerm2 or Terminal.app found")
}
