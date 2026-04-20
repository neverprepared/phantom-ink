# Reflex Hooks

## Timeout Rationale

Hooks use a project-default timeout of **5 seconds** for all PreToolUse and PostToolUse hooks — enough for lightweight shell operations and local IPC.

SessionStart hooks use higher timeouts because they perform heavier work at startup:

| Hook | Timeout | Reason |
|------|---------|--------|
| `check-dependencies.sh` | 10s | Checks multiple CLI tools and package managers; may spawn several subprocesses |
| `brainbox-hook.sh` | 15s | Makes an outbound HTTP call to the Brainbox API, which may be slow to respond on first connect |

## Hook Failure Modes

Each hook script is designed to **fail open** — non-zero exit codes are suppressed internally so that a hook failure never blocks a tool call. Scripts achieve this by:
- Catching errors and `exit 0` on all code paths
- Guarding with availability checks (e.g. `command -v jq`) before invoking external tools

## PostToolUse Matcher Design

- `langfuse-hook.sh` and `notify-hook.sh` use `matcher: ".*"` so they fire on every tool call (LangFuse traces all calls; notify evaluates the event type internally).
- `qdrant-websearch-hook.sh`, `memory-hook.sh` are scoped to `WebSearch|WebFetch` only since they are only meaningful for web results.
- `guardrail-hook.sh` is scoped to `Bash|Write|mcp__.*__jira_delete_issue|...` since it only evaluates write/destructive operations.
