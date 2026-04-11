---
description: Show Qdrant vector database connection status
allowed-tools: Bash(curl:*), Bash(echo:*)
argument-hint: [status]
---

# Qdrant Connection Status

Show connection status and health for the Qdrant vector database. Qdrant tools are always available when the MCP server is registered — if Qdrant isn't running, the MCP server handles the failure gracefully.

## Instructions

Run a health check against the configured Qdrant endpoint:

```bash
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
echo "**Qdrant Status**"
echo "  Endpoint: ${QDRANT_URL}"
echo ""
echo "**Health Check:**"
if curl -s "${QDRANT_URL}/readyz" >/dev/null 2>&1; then
    echo "  ✓ Qdrant is healthy and ready"
    # Get collections count
    COLLECTIONS=$(curl -s "${QDRANT_URL}/collections" 2>/dev/null | grep -o '"count":[0-9]*' | cut -d: -f2)
    if [ -n "$COLLECTIONS" ]; then
        echo "  Collections: ${COLLECTIONS}"
    fi
else
    echo "  ✗ Qdrant is not responding"
    echo "  Set QDRANT_URL if using a different endpoint (default: http://localhost:6333)"
fi
```
