# Known Issues

## WebSearch Auto-Storage in Containers (Regression)

**Status:** Identified, needs debugging
**Severity:** Medium
**Affects:** Brainbox containers (developer, researcher, performer)
**Created:** 2026-02-14

### Description

The WebSearch auto-storage hook (`qdrant-websearch-hook.sh`) successfully captures and stores search results when running on the host machine but fails silently when running inside brainbox containers.

### Expected Behavior

When a WebSearch is executed in a containerized Claude Code session:
1. PostToolUse hook triggers after WebSearch completion
2. `qdrant-websearch-hook.sh` filters for WebSearch tool calls
3. `qdrant-websearch-store.py` extracts metadata and synthesizes content
4. Data is stored in Qdrant with 384-dim embeddings
5. Qdrant collection point count increases

### Actual Behavior

- Hook script executes without errors
- Python script completes successfully (exit code 0)
- Qdrant connection test passes
- **No data is stored in Qdrant**
- Point count remains unchanged
- No error output visible (suppressed by `2>/dev/null`)

### Reproduction

1. Start a researcher container:
   ```bash
   just bb-docker-start -r researcher -s test-session -n
   ```

2. In the container's Claude session (http://localhost:7681):
   ```
   Search the web for: "test query"
   ```

3. Verify no storage occurred:
   ```bash
   curl -s https://qdrant.neverprepared.com:443/collections/personal_memories | jq '.result.points_count'
   # Count should increase but doesn't
   ```

### Environment

- **Host:** macOS Darwin 25.2.0
- **Container:** Ubuntu 24.04 (brainbox-researcher)
- **Python:** 3.12 (via uvx)
- **Qdrant:** Remote instance at https://qdrant.neverprepared.com:443
- **Collection:** personal_memories (determined by WORKSPACE_PROFILE)

### Verification

**Working on host:**
```bash
cat << 'EOF' | QDRANT_URL=https://qdrant.neverprepared.com:443 \
  uvx --python 3.12 --with qdrant-client --with fastembed \
  python reflex/plugins/reflex/scripts/qdrant-websearch-store.py
{"tool_name":"WebSearch","tool_input":{"query":"test"},"tool_response":[{"title":"Test","url":"https://test.com","snippet":"test"}]}
EOF
# ✅ Data stored successfully
```

**Failing in container:**
```bash
docker exec researcher-test bash -c 'cat << EOF | QDRANT_URL=... uvx ... python /home/developer/.claude/hooks/qdrant-websearch-store.py
{"tool_name":"WebSearch",...}
EOF'
# ❌ No data stored (script exits 0)
```

### Investigation Findings

1. **Qdrant Connectivity:** ✅ Container can reach Qdrant
   ```bash
   docker exec <container> uvx --with qdrant-client python -c "from qdrant_client import QdrantClient; ..."
   # Successfully lists collections
   ```

2. **Environment Variables:** ✅ Correctly set
   - QDRANT_URL: https://qdrant.neverprepared.com:443
   - WORKSPACE_PROFILE: personal
   - Collection resolves to: personal_memories

3. **Hook Registration:** ✅ Properly configured
   ```json
   {
     "type": "command",
     "command": "/home/developer/.claude/hooks/qdrant-websearch-hook.sh",
     "timeout": 5
   }
   ```

4. **Script Execution:** ✅ Runs without errors
   - No Python exceptions
   - No bash errors
   - Exit code: 0

5. **Fail-Open Design:** ⚠️ Masks root cause
   - All exceptions caught in `store_to_qdrant()`
   - stderr redirected to `/dev/null`
   - Makes debugging difficult

### Possible Causes

1. **SSL Certificate Validation**
   - Container may not trust the SSL certificate for qdrant.neverprepared.com
   - Python qdrant-client might be failing on certificate verification

2. **Network/Firewall Issues**
   - Container networking might have restrictions
   - HTTPS write operations might be blocked differently than reads

3. **Permission/Authentication**
   - Qdrant might require API key for write operations
   - Read operations succeed but writes fail silently

4. **uvx Environment Isolation**
   - Environment variables might not propagate correctly to uvx subprocess
   - QDRANT_URL might not be visible in the Python script's environment

5. **Embedding Generation**
   - FastEmbed model download/initialization might fail in container
   - Embedding generation silently fails before storage attempt

### Next Steps

1. **Remove error suppression** temporarily for debugging:
   ```bash
   # In qdrant-websearch-hook.sh, change:
   2>/dev/null || true
   # To:
   2>&1
   ```

2. **Add debug logging** to Python script:
   ```python
   import sys
   print(f"DEBUG: Starting storage...", file=sys.stderr)
   ```

3. **Test with explicit error handling**:
   - Create test version without try/except suppression
   - Run in container and capture actual errors

4. **Verify SSL certificates**:
   ```bash
   docker exec <container> curl -v https://qdrant.neverprepared.com:443
   ```

5. **Test with API key** if Qdrant requires authentication:
   ```bash
   export QDRANT_API_KEY=<key>
   ```

### Workaround

For now, WebSearch auto-storage works in:
- ✅ Host machine Claude Code sessions with reflex plugin
- ✅ Direct script execution outside containers

Containerized sessions should manually store important searches using:
```
Tool: qdrant-store
Information: "<synthesized content>"
Metadata: {...}
```

### Priority

Medium - Feature is implemented and works on host. Container support is a nice-to-have for brainbox isolation but not blocking for reflex plugin users.

### Related Files

- `reflex/plugins/reflex/scripts/qdrant-websearch-hook.sh`
- `reflex/plugins/reflex/scripts/qdrant-websearch-store.py`
- `docker/brainbox/Dockerfile.researcher`
- `docker/brainbox/setup/researcher/settings.json`
