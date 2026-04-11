---
description: Show LangFuse observability status
allowed-tools: Bash(echo:*), Bash(curl:*)
argument-hint: [status]
---

# LangFuse Integration

Show LangFuse observability status and configuration. Tracing is always active when credentials are present — no toggle needed. The PostToolUse hook exits silently when credentials are missing or LangFuse is unreachable.

## Instructions

### status (or no argument)

```bash
echo "**LangFuse Observability**"
echo ""
echo "**Configuration:**"
echo "- Host: ${LANGFUSE_BASE_URL:-http://localhost:3000}"
echo "- Public Key: ${LANGFUSE_PUBLIC_KEY:-<not set>}"
echo "- Secret Key: ${LANGFUSE_SECRET_KEY:+<set>}${LANGFUSE_SECRET_KEY:-<not set>}"
echo ""
if [ -n "${LANGFUSE_PUBLIC_KEY:-}" ] && [ -n "${LANGFUSE_SECRET_KEY:-}" ]; then
    echo "**Status:** Active (credentials present)"
    # Check reachability
    BASE_URL="${LANGFUSE_BASE_URL:-http://localhost:3000}"
    if curl -sf --max-time 3 "$BASE_URL/api/public/health" > /dev/null 2>&1; then
        echo "**Connectivity:** Reachable at $BASE_URL"
    else
        echo "**Connectivity:** Unreachable at $BASE_URL (traces will be dropped)"
    fi
else
    echo "**Status:** Inactive (missing credentials)"
    echo ""
    echo "Set these environment variables to enable tracing:"
    echo "  LANGFUSE_PUBLIC_KEY"
    echo "  LANGFUSE_SECRET_KEY"
    echo "  LANGFUSE_BASE_URL (optional, defaults to http://localhost:3000)"
fi
```

### Invalid argument

If an invalid argument is provided, show usage:

```
Usage: /reflex:langfuse [status]

Show LangFuse observability status and configuration.
Tracing is always active when credentials are present.
```
