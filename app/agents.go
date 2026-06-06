package main

import (
	"bytes"
	"context"
	"os/exec"
	"strings"
	"time"
)

// AgentInvocation describes how to invoke a CLI agent with a prompt as part
// of a chain step. PromptMode controls how the prompt text reaches the binary:
//
//   - "arg":   appended as a single trailing argument after PromptArgs
//   - "stdin": piped to stdin; PromptArgs are passed as-is
//
// OutputMode hints to the runner what to expect back:
//
//   - "stdout":       textual response on stdout only (gemini-style)
//   - "stdout+files": textual response on stdout AND filesystem changes in cwd
//     (claude/codex/aider/opencode); the runner captures stdout
//     and surfaces cwd as the working tree for subsequent steps.
type AgentInvocation struct {
	PromptArgs []string `json:"prompt_args"`
	PromptMode string   `json:"prompt_mode"`
	AcceptsCwd bool     `json:"accepts_cwd"`
	OutputMode string   `json:"output_mode"`
}

// AgentDescriptor describes a coding-agent CLI we know how to detect.
type AgentDescriptor struct {
	ID          string          `json:"id"`
	Binary      string          `json:"binary"`
	Label       string          `json:"label"`
	VersionArgs []string        `json:"-"`
	Invocation  AgentInvocation `json:"invocation"`
}

// DetectedAgent is a single row in the Agents panel — descriptor metadata plus
// whatever we found (or didn't find) on the user's PATH at scan time.
type DetectedAgent struct {
	ID         string          `json:"id"`
	Binary     string          `json:"binary"`
	Label      string          `json:"label"`
	Path       string          `json:"path"`
	Version    string          `json:"version"`
	Enabled    bool            `json:"enabled"`
	Detected   bool            `json:"detected"`
	DetectedAt string          `json:"detected_at"`
	Invocation AgentInvocation `json:"invocation"`
}

// Chainable reports whether the agent has a wired invocation and is therefore
// eligible to appear as a chain step. Used by the frontend chain builder.
func (d DetectedAgent) Chainable() bool {
	return d.Invocation.PromptMode != ""
}

// knownAgents is the catalog. Add new entries here to expand detection.
//
// Invocation values describe how each CLI takes a prompt for chain execution.
// Not every agent supports every chain feature — gemini, for example, prints
// to stdout but does not edit files in cwd.
var knownAgents = []AgentDescriptor{
	{
		ID: "claude", Binary: "claude", Label: "Claude Code",
		VersionArgs: []string{"--version"},
		Invocation: AgentInvocation{
			PromptArgs: []string{"-p"}, PromptMode: "arg", AcceptsCwd: true, OutputMode: "stdout+files",
		},
	},
	{
		ID: "codex", Binary: "codex", Label: "OpenAI Codex",
		VersionArgs: []string{"--version"},
		Invocation: AgentInvocation{
			PromptArgs: []string{"exec"}, PromptMode: "arg", AcceptsCwd: true, OutputMode: "stdout+files",
		},
	},
	{
		ID: "aider", Binary: "aider", Label: "Aider",
		VersionArgs: []string{"--version"},
		Invocation: AgentInvocation{
			PromptArgs: []string{"--message", "--yes"}, PromptMode: "arg", AcceptsCwd: true, OutputMode: "stdout+files",
		},
	},
	{
		ID: "gemini", Binary: "gemini", Label: "Gemini CLI",
		VersionArgs: []string{"--version"},
		Invocation: AgentInvocation{
			PromptArgs: []string{"-p"}, PromptMode: "arg", AcceptsCwd: true, OutputMode: "stdout",
		},
	},
	{
		ID: "copilot", Binary: "copilot", Label: "GitHub Copilot CLI",
		VersionArgs: []string{"--version"},
		// `copilot` runs interactively by default; `-p` is its non-interactive
		// prompt mode, and `--allow-all-tools` is required to run scripted
		// (otherwise it waits on confirmation for every tool call). This wires
		// the standalone binary, not the `gh copilot` extension.
		Invocation: AgentInvocation{
			PromptArgs: []string{"--allow-all-tools", "-p"},
			PromptMode: "arg", AcceptsCwd: true, OutputMode: "stdout+files",
		},
	},
	{
		ID: "opencode", Binary: "opencode", Label: "opencode",
		VersionArgs: []string{"--version"},
		Invocation: AgentInvocation{
			PromptArgs: []string{"run"}, PromptMode: "arg", AcceptsCwd: true, OutputMode: "stdout+files",
		},
	},
}

// detectAgents probes every known agent on PATH and returns one row per
// descriptor. Not-found agents are returned with Detected=false so callers
// can render placeholder rows. Version probes are bounded by versionTimeout.
func detectAgents(ctx context.Context) []DetectedAgent {
	const versionTimeout = 2 * time.Second
	now := time.Now().UTC().Format(time.RFC3339)
	out := make([]DetectedAgent, 0, len(knownAgents))
	for _, d := range knownAgents {
		row := DetectedAgent{ID: d.ID, Binary: d.Binary, Label: d.Label, Invocation: d.Invocation}
		path, err := exec.LookPath(d.Binary)
		if err != nil {
			out = append(out, row)
			continue
		}
		row.Path = path
		row.Detected = true
		row.DetectedAt = now
		row.Version = probeVersion(ctx, path, d.VersionArgs, versionTimeout)
		out = append(out, row)
	}
	return out
}

// probeVersion runs `<binary> <versionArgs...>` with a hard timeout and returns
// the first non-empty line. On any error or empty output the version is "".
func probeVersion(parent context.Context, binary string, args []string, timeout time.Duration) string {
	if len(args) == 0 {
		return ""
	}
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, binary, args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil && stdout.Len() == 0 {
		// Some agents print version to stderr; fall through to use it.
		if stderr.Len() == 0 {
			return ""
		}
	}
	combined := stdout.String()
	if combined == "" {
		combined = stderr.String()
	}
	for _, line := range strings.Split(combined, "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			return line
		}
	}
	return ""
}
