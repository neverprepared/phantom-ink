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

// localRunner is an in-process runner that executes work items by spawning
// `claude --dangerously-skip-permissions` subprocesses on the local Mac host.
// It registers with the remote brainbox API and polls for pending work items.
type localRunner struct {
	client    *brainbox.Client
	name      string
	machineID string

	longPoll *http.Client

	stopCh  chan struct{}
	stopped chan struct{}

	procs sync.Map // workID → *exec.Cmd
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

	// Register with exponential backoff before entering poll loop.
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

		// Check stop before blocking on long-poll.
		select {
		case <-ctx.Done():
			return
		case <-r.stopCh:
			return
		default:
		}

		item, err := r.client.GetPendingWork(r.name, r.longPoll)
		if err != nil {
			// Transient error (network, timeout, 404) — brief pause then retry.
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
		Capabilities: map[string]bool{
			"docker":         true, // backend capability the API checks before routing
			"session.create": true,
			"session.query":  true,
			"session.stop":   true,
			"session.delete": true,
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
	case "session.create", "session.query":
		result = r.execClaude(ctx, item)
	case "session.stop", "session.delete":
		result = r.killProc(item.ID)
	default:
		result = brainbox.RunnerResult{OK: false, Error: fmt.Sprintf("unsupported kind: %s", item.Kind)}
	}

	if err := r.client.PostRunnerResult(r.name, item.ID, result); err != nil {
		fmt.Printf("local runner post result (work %s): %v\n", item.ID, err)
	}
}

func (r *localRunner) execClaude(ctx context.Context, item *brainbox.RunnerWorkItem) brainbox.RunnerResult {
	task, _ := item.Payload["task_description"].(string)
	if task == "" {
		task, _ = item.Payload["prompt"].(string)
	}
	if task == "" {
		return brainbox.RunnerResult{OK: false, Error: "missing task_description or prompt"}
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

	cmd := exec.CommandContext(execCtx, "claude", "--dangerously-skip-permissions", "-p", task)
	cmd.Dir = workDir

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	r.procs.Store(item.ID, cmd)
	defer r.procs.Delete(item.ID)

	if err := cmd.Run(); err != nil {
		errMsg := strings.TrimSpace(stderr.String())
		if errMsg == "" {
			errMsg = err.Error()
		}
		return brainbox.RunnerResult{OK: false, Error: errMsg}
	}

	output := strings.TrimSpace(stdout.String())
	return brainbox.RunnerResult{
		OK: true,
		Data: map[string]any{
			"output": output,
		},
	}
}

func (r *localRunner) killProc(workID string) brainbox.RunnerResult {
	if v, ok := r.procs.Load(workID); ok {
		if cmd, ok := v.(*exec.Cmd); ok && cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
		r.procs.Delete(workID)
	}
	return brainbox.RunnerResult{OK: true}
}
