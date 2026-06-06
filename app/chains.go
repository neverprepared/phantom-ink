package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// stepRunTimeout is the hard wall-clock cap on a single chain step. Anything
// past this gets SIGKILL'd. Long jobs should run inside a brainbox session
// rather than the host chain runner.
const stepRunTimeout = 5 * time.Minute

// Chain is the runtime form of a saved chain. It mirrors ChainRow but with
// Steps and Files materialized as slices.
//
// Files are stored as paths relative to the profile's workspace_home so the
// same chain works across profiles — "code/api/main.go" resolves under
// whatever profile is active at run time. At render time the {{files}}
// template variable expands to the shell-quoted absolute paths.
type Chain struct {
	ID               string          `json:"id"`
	Name             string          `json:"name"`
	Description      string          `json:"description"`
	Steps            []ChainStep     `json:"steps"`
	Cwd              string          `json:"cwd"`
	OnSuccess        []ChainFollowup `json:"on_success"`
	Files            []string        `json:"files"`
	WorkspaceProfile string          `json:"workspace_profile"`
	CreatedAt        string          `json:"created_at"`
	UpdatedAt        string          `json:"updated_at"`
}

// ChainFollowup is a declarative spec for enqueueing a follow-up task when a
// chain run completes successfully. InputFrom controls what fills the next
// chain's {{input}} slot:
//
//   - "stdout":  the last step's stdout
//   - "literal": the InputLiteral field, verbatim
//   - "":        defaults to "stdout"
//
// Future extensions (template rendering, conditional branching) go here.
type ChainFollowup struct {
	ChainID      string `json:"chain_id"`
	InputFrom    string `json:"input_from"`
	InputLiteral string `json:"input_literal"`
	Cwd          string `json:"cwd"`
}

// ChainStep is one node in a chain. PromptTemplate may reference {{input}}
// (the initial user input) and {{prev.output}} (the previous step's stdout).
// Cwd overrides the chain-level cwd for this step; empty means inherit.
//
// Type selects the step kind:
//
//   - "agent" (default/empty): run an agent binary on the host via AgentID
//   - "playbook": trigger a brainbox playbook run via PlaybookID, block until
//     the playbook reaches a terminal state (completed/failed/cancelled)
//
// Executor (agent steps only) selects the execution backend. Only "host" is
// wired today. Reserved: "session", "queue".
type ChainStep struct {
	Type           string `json:"type"`       // "agent" (default) | "playbook"
	AgentID        string `json:"agent_id"`   // non-empty when type="agent"
	PlaybookID     string `json:"playbook_id"` // non-empty when type="playbook"
	PromptTemplate string `json:"prompt_template"`
	Cwd            string `json:"cwd"`
	Executor       string `json:"executor"`
}

// ChainRunEvent is the payload streamed to the frontend via EventsEmit during
// a chain run. The frontend consumes these to render live progress.
type ChainRunEvent struct {
	RunID     string `json:"run_id"`
	ChainID   string `json:"chain_id"`
	Phase     string `json:"phase"`     // "run:start" | "step:start" | "step:output" | "step:done" | "run:done"
	StepIndex int    `json:"step_index"`
	AgentID   string `json:"agent_id"`
	Output    string `json:"output"`  // for step:output / step:done — accumulated stdout
	Stderr    string `json:"stderr"`  // captured stderr (final on step:done)
	ExitCode  int    `json:"exit_code"`
	Error     string `json:"error"`   // non-empty when step failed
	Status    string `json:"status"`  // "running" | "success" | "failed" | "cancelled"
	At        string `json:"at"`      // RFC3339 timestamp
}

// renderPromptTemplate substitutes the supported placeholders into a step's
// template. Unknown placeholders are left alone so the user can see them in
// the prompt if they typo'd.
//
// Supported:
//   - {{input}}       initial chain input
//   - {{prev.output}} previous step's stdout
//   - {{files}}       space-separated, shell-quoted absolute paths of the
//                     chain's attached files (resolved to absolute under the
//                     active profile's workspace_home before being passed in)
func renderPromptTemplate(tpl, input, prev, files string) string {
	tpl = strings.ReplaceAll(tpl, "{{input}}", input)
	tpl = strings.ReplaceAll(tpl, "{{prev.output}}", prev)
	tpl = strings.ReplaceAll(tpl, "{{files}}", files)
	return tpl
}

// shellQuote returns s wrapped so it's safe as a single shell-arg even when
// it contains spaces, quotes, or other punctuation. Single-quote escape:
// every internal ' becomes '\'' and the whole string is wrapped in '...'.
func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

// newRunID is a short opaque identifier for a chain run.
func newRunID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return "run-" + hex.EncodeToString(b[:])
}

// runChainStep executes a single agent invocation on the host and returns the
// captured stdout + stderr + exit code. Bounded by stepRunTimeout.
//
// TODO: sandbox — this runs as a host subprocess. Future iterations should
// route execution into a brainbox container or a UTM VM based on chain config.
// See memory:project_agent_chain_sandbox_todo.md for the design backlog.
func runChainStep(parent context.Context, desc AgentDescriptor, prompt, cwd string) (stdout, stderr string, exitCode int, err error) {
	if desc.Invocation.PromptMode == "" {
		return "", "", -1, fmt.Errorf("agent %q has no invocation wired", desc.ID)
	}

	ctx, cancel := context.WithTimeout(parent, stepRunTimeout)
	defer cancel()

	args := append([]string{}, desc.Invocation.PromptArgs...)
	if desc.Invocation.PromptMode == "arg" {
		args = append(args, prompt)
	}

	cmd := exec.CommandContext(ctx, desc.Binary, args...)
	if cwd != "" && desc.Invocation.AcceptsCwd {
		cmd.Dir = cwd
	}
	if desc.Invocation.PromptMode == "stdin" {
		cmd.Stdin = strings.NewReader(prompt)
	}

	var stdoutBuf, stderrBuf bytes.Buffer
	cmd.Stdout = &stdoutBuf
	cmd.Stderr = &stderrBuf

	runErr := cmd.Run()
	stdout = stdoutBuf.String()
	stderr = stderrBuf.String()
	if cmd.ProcessState != nil {
		exitCode = cmd.ProcessState.ExitCode()
	} else {
		exitCode = -1
	}
	if runErr != nil {
		return stdout, stderr, exitCode, runErr
	}
	return stdout, stderr, exitCode, nil
}

// chainStepsFromJSON unmarshals a chain row's StepsJSON into structured steps.
func chainStepsFromJSON(s string) ([]ChainStep, error) {
	if s == "" {
		return nil, nil
	}
	var steps []ChainStep
	if err := json.Unmarshal([]byte(s), &steps); err != nil {
		return nil, fmt.Errorf("decode chain steps: %w", err)
	}
	return steps, nil
}

// chainStepsToJSON marshals steps to the form persisted in the chains table.
func chainStepsToJSON(steps []ChainStep) (string, error) {
	if steps == nil {
		steps = []ChainStep{}
	}
	b, err := json.Marshal(steps)
	if err != nil {
		return "", fmt.Errorf("encode chain steps: %w", err)
	}
	return string(b), nil
}

func chainFollowupsFromJSON(s string) ([]ChainFollowup, error) {
	if s == "" {
		return nil, nil
	}
	var out []ChainFollowup
	if err := json.Unmarshal([]byte(s), &out); err != nil {
		return nil, fmt.Errorf("decode chain followups: %w", err)
	}
	return out, nil
}

func chainFollowupsToJSON(fs []ChainFollowup) (string, error) {
	if fs == nil {
		fs = []ChainFollowup{}
	}
	b, err := json.Marshal(fs)
	if err != nil {
		return "", fmt.Errorf("encode chain followups: %w", err)
	}
	return string(b), nil
}

func chainFilesFromJSON(s string) ([]string, error) {
	if s == "" {
		return nil, nil
	}
	var out []string
	if err := json.Unmarshal([]byte(s), &out); err != nil {
		return nil, fmt.Errorf("decode chain files: %w", err)
	}
	return out, nil
}

func chainFilesToJSON(files []string) (string, error) {
	if files == nil {
		files = []string{}
	}
	b, err := json.Marshal(files)
	if err != nil {
		return "", fmt.Errorf("encode chain files: %w", err)
	}
	return string(b), nil
}
