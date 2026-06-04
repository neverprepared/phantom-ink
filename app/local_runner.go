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

	procs    sync.Map // sessionName → *exec.Cmd (for session.stop/delete)
	workDirs sync.Map // sessionName → string (working dir from session.create)
	tabs     sync.Map // playbookKey → tty string (reuse one tab per playbook run)
	accums   sync.Map // playbookKey → *stepAccum (batch steps before sending)
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
		Host:          "local-process",
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
	case "session.exec":
		// _wait_for_session probes the container with exec before sending the
		// real query. For local sessions there is no container, so we return
		// the "claude_ready" sentinel immediately so the wait loop unblocks.
		result = brainbox.RunnerResult{OK: true, Data: map[string]any{"output": "claude_ready", "success": true}}
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

// handleSessionCreate registers a local session and returns a SessionContext
// immediately. No terminal is opened here — the terminal opens during
// session.query when the actual task runs visibly.
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

	// Remember the working directory so session.query can use it.
	if sessionName != "" {
		r.workDirs.Store(sessionName, workDir)
	}

	// Open a terminal tab once per playbook run with Claude running interactively.
	// cd first so direnv fires and exports env vars before Claude starts.
	key := playbookKey(sessionName)
	if _, exists := r.tabs.Load(key); !exists {
		if tty, err := openTabGetTTY(); err == nil && tty != "" {
			r.tabs.Store(key, tty)
			time.Sleep(300 * time.Millisecond) // let the shell initialize
			_ = writeToTab(tty, fmt.Sprintf("cd %q && claude --dangerously-skip-permissions", workDir))
		}
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

// stepAccum batches playbook step prompts that arrive in a quick burst (now
// that _wait_for_session is skipped for runner sessions) and fires a single
// instruction to Claude once the burst settles.
type stepAccum struct {
	mu     sync.Mutex
	steps  []string
	timer  *time.Timer
	runner *localRunner
	key    string
}

// flushDelay is how long after the last step arrives before flushing. Must be
// longer than the remote API round-trip time (create→query→stop→delete) so all
// steps from a single playbook run land in the same batch before we fire.
const flushDelay = 3 * time.Second

func (a *stepAccum) add(step string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.steps = append(a.steps, step)
	if a.timer != nil {
		a.timer.Reset(flushDelay)
	}
}

func (a *stepAccum) flush() {
	a.mu.Lock()
	steps := make([]string, len(a.steps))
	copy(steps, a.steps)
	a.mu.Unlock()

	a.runner.accums.Delete(a.key)

	tabTTY := ""
	if v, ok := a.runner.tabs.Load(a.key); ok {
		tabTTY, _ = v.(string)
	}
	if tabTTY == "" || len(steps) == 0 {
		return
	}

	// Give Claude time to finish starting up before sending the prompt.
	time.Sleep(2 * time.Second)
	if err := writeToTab(tabTTY, strings.Join(steps, "\n\n")); err != nil {
		fmt.Printf("local runner: write to tab: %v\n", err)
		a.runner.tabs.Delete(a.key)
	}
}

// handleSessionQuery accumulates the step prompt and returns immediately.
// Once the burst of steps stops, stepAccum.flush writes one file and sends
// a single instruction to Claude.
func (r *localRunner) handleSessionQuery(_ context.Context, item *brainbox.RunnerWorkItem) brainbox.RunnerResult {
	prompt, _ := item.Payload["prompt"].(string)
	if prompt == "" {
		prompt, _ = item.Payload["task_description"].(string)
	}
	if prompt == "" {
		return brainbox.RunnerResult{OK: false, Error: "missing prompt"}
	}

	sessionName, _ := item.Payload["session_name"].(string)
	key := playbookKey(sessionName)

	tabTTY := ""
	if v, ok := r.tabs.Load(key); ok {
		tabTTY, _ = v.(string)
	}
	if tabTTY == "" {
		workDir := r.resolveWorkDir(sessionName, item)
		ttl := resolveTTL(item)
		return r.runQuerySubprocess(context.Background(), prompt, workDir, sessionName, ttl)
	}

	actual, _ := r.accums.LoadOrStore(key, &stepAccum{runner: r, key: key})
	accum := actual.(*stepAccum)
	accum.add(prompt)

	accum.mu.Lock()
	if accum.timer == nil {
		accum.timer = time.AfterFunc(flushDelay, accum.flush)
	}
	accum.mu.Unlock()

	return brainbox.RunnerResult{OK: true, Data: map[string]any{"output": ""}}
}

func (r *localRunner) resolveWorkDir(sessionName string, item *brainbox.RunnerWorkItem) string {
	if sessionName != "" {
		if v, ok := r.workDirs.Load(sessionName); ok {
			if d, _ := v.(string); d != "" {
				return d
			}
		}
	}
	if d, _ := item.Payload["workspace_home"].(string); d != "" {
		return d
	}
	return os.Getenv("HOME")
}

func resolveTTL(item *brainbox.RunnerWorkItem) time.Duration {
	if v, ok := item.Payload["ttl"].(float64); ok && v > 0 {
		return time.Duration(v) * time.Second
	}
	return 600 * time.Second
}

// runQuerySubprocess is the fallback when no terminal app is available. Runs
// claude -p as a background subprocess and captures output directly.
func (r *localRunner) runQuerySubprocess(ctx context.Context, prompt, workDir, sessionName string, ttl time.Duration) brainbox.RunnerResult {
	execCtx, cancel := context.WithTimeout(ctx, ttl)
	defer cancel()

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
		r.workDirs.Delete(sessionName)
	}
	return brainbox.RunnerResult{OK: true}
}

// playbookKey extracts a group key from a session name so that all steps of
// the same playbook run share a single terminal tab.
// "pb-abc123-t0" → "pb-abc123", anything else → sessionName unchanged.
func playbookKey(sessionName string) string {
	parts := strings.Split(sessionName, "-")
	if len(parts) >= 3 && parts[0] == "pb" {
		return parts[0] + "-" + parts[1]
	}
	return sessionName
}

// openTabGetTTY opens a new iTerm2 tab and returns its TTY path.
func openTabGetTTY() (string, error) {
	script := `
try
	tell application "iTerm2"
		activate
		if (count of windows) = 0 then
			create window with default profile
		end if
		tell current window
			create tab with default profile
			return tty of current session of current tab
		end tell
	end tell
on error err
	return ""
end try`
	out, err := exec.Command("osascript", "-e", script).Output()
	if err != nil {
		return "", err
	}
	tty := strings.TrimSpace(string(out))
	if tty == "" {
		return "", fmt.Errorf("empty TTY returned from iTerm2")
	}
	// Normalize: iTerm2 may return "ttys001" or "/dev/ttys001".
	if !strings.HasPrefix(tty, "/dev/") {
		tty = "/dev/" + tty
	}
	return tty, nil
}

// writeToTab sends a line of text (followed by Enter) to the iTerm2 session
// identified by tty. The cmd must not contain newlines.
func writeToTab(tty, cmd string) error {
	// Escape backslashes and double-quotes for AppleScript string literal.
	escaped := strings.ReplaceAll(cmd, `\`, `\\`)
	escaped = strings.ReplaceAll(escaped, `"`, `\"`)

	script := fmt.Sprintf(`
try
	tell application "iTerm2"
		repeat with w in windows
			repeat with t in tabs of w
				repeat with s in sessions of t
					if tty of s is "%s" then
						tell s
							write text "%s" newline NO
							delay 0.05
							write text "" newline YES
						end tell
						return "ok"
					end if
				end repeat
			end repeat
		end repeat
	end tell
	return "not_found"
on error
	return "err"
end try`, tty, escaped)

	out, err := exec.Command("osascript", "-e", script).Output()
	if err != nil {
		return err
	}
	result := strings.TrimSpace(string(out))
	if result != "ok" {
		return fmt.Errorf("writeToTab: %s (tty=%s)", result, tty)
	}
	return nil
}

// openLocalSessionTab opens a new terminal tab running `claude --dangerously-skip-permissions`
// interactively in workDir. Called for user-initiated local sessions.
func openLocalSessionTab(workDir string) error {
	if workDir == "" {
		workDir = "~"
	}
	// Write a tiny script so we don't embed workDir (which may contain
	// double-quotes) directly in the AppleScript write text call.
	f, err := os.CreateTemp("", "local-session-*.sh")
	if err != nil {
		return err
	}
	scriptPath := f.Name()
	fmt.Fprintf(f, "#!/bin/bash\ncd %q\nexec claude --dangerously-skip-permissions\n", workDir)
	f.Chmod(0755)
	f.Close()
	return runInNewTerminalTab("bash " + scriptPath)
	// Script file is intentionally not removed — it lives until the session ends.
}

// runInNewTerminalTab opens a new iTerm2 (or Terminal.app) tab and runs cmd.
// cmd must contain no double-quote characters (use a script file path instead).
func runInNewTerminalTab(cmd string) error {
	// Reject any double-quotes in cmd — callers must use a script file path.
	if strings.ContainsRune(cmd, '"') {
		return fmt.Errorf("runInNewTerminalTab: cmd must not contain double-quotes")
	}

	// Try iTerm2 first. Tell it directly — no System Events process-existence
	// guard, which can silently fail when iTerm2 is running but not frontmost.
	itermScript := fmt.Sprintf(`
try
	tell application "iTerm2"
		activate
		if (count of windows) = 0 then
			create window with default profile
		end if
		tell current window
			create tab with default profile
			tell current session of current tab
				write text "%s"
			end tell
		end tell
	end tell
	return "ok"
on error
	return "err"
end try`, cmd)

	out, err := exec.Command("osascript", "-e", itermScript).Output()
	if err == nil && strings.TrimSpace(string(out)) == "ok" {
		return nil
	}

	// Fall back to Terminal.app.
	termScript := fmt.Sprintf(`
try
	tell application "Terminal"
		activate
		do script "%s"
	end tell
	return "ok"
on error
	return "err"
end try`, cmd)

	out, err = exec.Command("osascript", "-e", termScript).Output()
	if err == nil && strings.TrimSpace(string(out)) == "ok" {
		return nil
	}

	return fmt.Errorf("no iTerm2 or Terminal.app available")
}
