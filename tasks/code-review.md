# Code Review — ink-bunny Monorepo

Reviewer: Claude Code (senior-engineer pass)
Date: 2026-03-11
Scope: brainbox/, reflex/, docker/, Formula/

---

## 1. brainbox/ — FastAPI Backend

### HIGH — Bugs / Security / Correctness

**BB-H1. Session name not validated on `/api/stop` and `/api/delete`**
`api.py:497-554`
`body.name` is passed to `_extract_session_name()` (which only strips role prefixes) then
used to look up/stop containers without calling `validate_session_name()`. A crafted name
containing shell-special characters or path components could reach Docker container lookup
without sanitisation. Other endpoints (exec, query, get) do call `validate_session_name`.
Fix: add `validate_session_name(body.name)` at the top of both handlers.

**BB-H2. `/api/create` returns HTTP 200 on failure**
`api.py:717-720`
```python
return {"success": False, "error": str(exc)}
```
All exceptions bubble up as `{"success": False}` with a 200 status code. API clients
cannot rely on HTTP status codes to detect errors; they must inspect the body. Use
`raise HTTPException(status_code=500, …)` instead.

**BB-H3. SSE endpoint `/api/events` is unauthenticated**
`api.py:434-463`
Docker events (container create/start/stop/die/destroy) and hub state changes (task
submissions, agent spawns, token events) are broadcast to any unauthenticated client that
connects. Behind localhost-only binding this is a lower risk, but the nginx UI container
publicly proxies the SSE endpoint to any browser origin. Add `_key=Depends(require_api_key)`
or at minimum validate the hub Bearer token.

**BB-H4. `inject_config_bundle` chown exec fails silently on stopped container**
`backends/docker.py:548-572`
`put_archive()` works on a stopped container, but the subsequent `exec_run(["sh","-c","chown …"])` requires a running container. At the time `inject_config_bundle` is called in `run_pipeline` (before `configure()` starts the container), the container is not yet running. The `exec_run` fails silently (try/except with a warning log), leaving all injected `.claude/` files owned by root. Claude Code inside the container will not be able to read them.

Fix: either start the container before injection, or run the chown inside `configure()` after `container.start()`.

**BB-H5. Race condition in port allocation**
`lifecycle.py:419-437`
`_find_available_port()` queries running containers for used ports, then returns the first
free one. There is no lock. Two concurrent `/api/create` requests can both receive the same
free port before either container is started. One container will fail to bind.
Fix: use a module-level lock around the scan-and-reserve operation, or use OS-level socket probing (bind, check, release).

**BB-H6. Remote Docker client never closed — connection leak**
`backends/docker.py:27-32`
When `docker_host` is not None, a fresh `docker.DockerClient` is created on every SDK
call and is never closed. Each client holds open HTTP connections to the remote daemon.
Under sustained load this exhausts file descriptors.
Fix: cache one client per `docker_host` string with proper lifecycle, or use a context manager.

**BB-H7. Secret written with trailing newline via `echo`**
`backends/docker.py:194`
```python
f"echo {shlex.quote(value)} > /run/secrets/{safe_name} …"
```
`echo` appends a `\n` to the value. When code reads the secret back it gets a trailing
newline. ANTHROPIC_API_KEY with a trailing newline will fail authentication.
Fix: use `printf '%s'` instead of `echo`.

**BB-H8. TOCTOU race in profile cache permission enforcement**
`lifecycle.py:106-114`
```python
mode = cache_env.stat().st_mode
if mode & stat.S_IROTH:
    …
    cache_env.chmod(0o600)
```
Another process can read the world-readable file between `stat()` and `chmod()`. This
leaks environment secrets (API keys, tokens) on multi-user hosts.

**BB-H9. `_add_dir_translated` follows symlinks**
`bundle.py:155`
`src_dir.rglob("*")` follows symlinks by default. If any path inside `~/.claude` is a
symlink pointing outside the directory (e.g. `~/.claude/hooks -> /tmp/attacker`), the
bundle will include and path-translate arbitrary host files, then inject them into
every provisioned container.
Fix: skip symlinks with `if item.is_symlink(): continue`.

**BB-H10. `/api/hub/agents` endpoints are unauthenticated**
`api.py:1221-1231`
`hub_list_agents` and `hub_get_agent` expose all agent names, roles, and role-prompt
paths without requiring an API key. Add `_key=Depends(require_api_key)`.

**BB-H11. X-Forwarded-For spoofing bypasses rate limiting**
`backends/docker.py` → `rate_limit.py`
The nginx proxy (`docker/brainbox-ui/nginx.conf:14`) forwards `X-Forwarded-For` to the
brainbox API. `slowapi`'s `get_remote_address` uses this header when present, so an
attacker can spoof their IP and bypass the per-IP rate limits on create/stop/delete/exec.
Fix: configure `slowapi` to trust only the real socket address, or set `forwarded_for_trusted_proxies`.

---

### MEDIUM — Missing Error Handling / Edge Cases

**BB-M1. Fallback delete uses wrong container name**
`api.py:566-570`
```python
container_name = f"{settings.resolved_prefix}{session_name}"
```
`settings.resolved_prefix` is built from `settings.role` (default `"developer"`). If the
session was created with a non-default role, the fallback will construct the wrong
container name and raise `NotFound`.

**BB-M2. Hub state file written with default umask**
`hub.py:108-109`
```python
await asyncio.to_thread(tmp_file.write_text, content)
```
`write_text` uses the process umask (often 0o644). The state file contains tokens, task
IDs, and session metadata. Should write with `0o600`.

**BB-M3. `auth.py:write_secure_file` is not atomic**
`auth.py:24-28`
```python
path.write_text(content)
path.chmod(mode)
```
There is a window between `write_text` (creates with umask perms) and `chmod(0o600)` where
other processes can read the API key. Use `os.open` with `O_CREAT | O_WRONLY` and mode
`0o600` before writing.

**BB-M4. `subprocess.check_output(["brew", "--prefix"])` is PATH-dependent**
`bundle.py:62`
If the PATH environment is tampered with (e.g. inside the brainbox-api container), a
rogue `brew` binary could be executed. Use the full path (`/opt/homebrew/bin/brew`) or
catch and discard errors without calling subprocess at all.

**BB-M5. `_tmux_send_and_wait` injects prompt string unescaped into tmux**
`api.py:932-935`
```python
container.exec_run(["tmux", "send-keys", "-t", "main", prompt, "Enter"])
```
The prompt text is used directly. tmux interprets special key names (e.g. `M-w`, `C-a`)
inside the `send-keys` argument. A carefully crafted prompt could inject tmux commands.
Also `working_dir` is interpolated into `cd {working_dir}` without quoting, so directory
names with spaces or shell metacharacters will fail.

**BB-M6. `/api/sessions/{name}/exec` — no restriction on container targets**
`api.py:744-767`
Authenticated clients can exec arbitrary shell commands inside any managed container.
This is likely intentional for the orchestration use-case but should be clearly
documented as a privileged operation and considered for separate auth scope.

**BB-M7. No size limit on profile cache env file reads**
`lifecycle.py:119`, `lifecycle.py:383-397`
Both `_read_cache_vars` and `_resolve_profile_env` read the full `.env` cache file
without a size guard. A multi-megabyte file would be buffered entirely in memory for
every session provision.

**BB-M8. `_find_available_port` falls back to `start` port on any error**
`lifecycle.py:435-437`
Any exception from the Docker client causes the function to silently return `start`
(7681). If Docker is unreachable, every session will try to bind the same default port.

---

### LOW — Code Style / Quality

**BB-L1. Duplicate session-listing logic**
`api.py:369-426` vs `backends/docker.py:639-697`
`_get_sessions_info_legacy` in `api.py` is nearly identical to `DockerBackend.get_sessions_info`.
It is still called by `_get_sessions_info` (via the Docker backend), meaning most of the
work is done in the backend. The legacy copy should be removed.

**BB-L2. `_ROLE_PREFIXES` is hardcoded**
`api.py:322`
```python
_ROLE_PREFIXES = ("developer-", "researcher-", "performer-")
```
`_extract_session_name` only strips these three prefixes. Sessions created with custom
roles or a non-default `container_prefix` will not have their prefix stripped.

**BB-L3. `_translate` is O(k·n) per value**
`bundle.py:75-78`
Path substitutions are sorted on every string value traversal. With large configs and
many substitution pairs, this adds up. Pre-sort the `path_map` once before calling
`_translate`.

**BB-L4. Unused `threading` import**
`api.py:8`
`threading` is imported at module level but only used inside `_get_container_metrics`
where `_trace_cache_lock` is defined at module scope. The import is fine but `_trace_cache_lock`
could be documented near its definition.

**BB-L5. `_now_ms` / `_iso_now` defined at bottom of `lifecycle.py`**
`lifecycle.py:920-930`
These helpers are referenced throughout the module but defined at the end. Move them to
the top of the file with the other helpers for readability.

---

## 2. docker/ — Dockerfiles and Compose

### HIGH — Security / Correctness

**DK-H1. `curl | bash` installers without checksum verification**
`docker/brainbox/Dockerfile:45,61,74,100,163`
Homebrew, uv, nvm, rustup, and the Claude Code CLI are all installed via
`curl … | bash` without verifying signatures or checksums. A MITM or compromised CDN
can substitute malicious scripts. Use pinned versions and verify checksums before
executing.

**DK-H2. Passwordless sudo for all commands**
`docker/brainbox/Dockerfile:32`
```dockerfile
RUN echo 'developer ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/developer
```
This grants unlimited privilege escalation to any code running as `developer`. The
legitimate need is Homebrew installation (which needs to create `/home/linuxbrew`).
Scope the sudoers rule to the specific commands Homebrew needs, and remove it after
the Homebrew install layer.

**DK-H3. `chmod -R 777 /ms-playwright` — world-writable browser binaries**
`docker/brainbox/Dockerfile:174`
Any process in the container (including Claude Code) can replace browser binaries with
malicious ones. Use `chown -R developer:developer /ms-playwright` and `chmod -R 755`
instead.

**DK-H4. websocat installed from `latest` release with no checksum**
`docker/brainbox/Dockerfile:186-190`
`/releases/latest/download/websocat.${WSARCH}` always fetches the latest binary without
any integrity check. Pin to a specific release tag and verify the SHA256.

**DK-H5. `XDG_CONFIG_HOME` mounted read-write in docker-compose**
`docker-compose.yml:15`
```yaml
- "${XDG_CONFIG_HOME:-${HOME}/.config}:${XDG_CONFIG_HOME:-${HOME}/.config}"
```
The entire user config directory is mounted read-write. Brainbox only needs its own
`developer/` subdirectory. A compromised brainbox-api container could modify other
applications' config (e.g. VS Code settings, ssh_config in XDG-compliant programs).
Mount only the specific subdirectory:
```yaml
- "${XDG_CONFIG_HOME:-${HOME}/.config}/developer:${XDG_CONFIG_HOME:-${HOME}/.config}/developer"
```

**DK-H6. nginx forwards `X-Forwarded-For` unchecked — rate limit bypass**
`docker/brainbox-ui/nginx.conf:13-14`
As noted in BB-H11, nginx sets `X-Forwarded-For` which the brainbox API uses for rate
limiting. Clients can spoof this header.

**DK-H7. HEALTHCHECK only passes if `claude` process is running**
`docker/brainbox/Dockerfile:233-234`
```dockerfile
HEALTHCHECK CMD pgrep -x "claude" > /dev/null || exit 1
```
Claude Code is only started by the ttyd wrapper, not at container startup. Every
freshly-provisioned container will be unhealthy until Claude is manually launched.
Docker Compose and health-check-based tooling will report the service as unhealthy.
A better healthcheck would be to verify the ttyd port is listening.

---

### MEDIUM — Missing Error Handling / Edge Cases

**DK-M1. `qdrant/qdrant:latest` and other mutable image tags**
`docker-compose.yml`, `Formula/brainbox.rb`
`latest` tags are used for qdrant and brainbox images. These can silently pull breaking
changes on `docker compose pull`. Pin to specific digests or version tags.

**DK-M2. nginx has no `client_max_body_size` limit**
`docker/brainbox-ui/nginx.conf`
No upload size limit is set. Very large request bodies will reach the FastAPI backend
unchecked (the Python side has a per-endpoint limit on artifacts, but other endpoints
do not). Set `client_max_body_size 50m;` to match the artifact limit.

**DK-M3. nginx missing security headers**
`docker/brainbox-ui/nginx.conf`
The nginx config does not set:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Content-Security-Policy`
- `Referrer-Policy`
The dashboard SPA can be framed or content-sniffed by other origins.

**DK-M4. SSH agent socket path hardcoded for Docker Desktop (macOS only)**
`docker-compose.yml:34`
```yaml
- SSH_AUTH_SOCK=/run/host-services/ssh-auth.sock
```
This path is macOS-specific. On Linux the env var will be wrong and SSH agent forwarding
will silently fail. The variable should be set conditionally or from `$SSH_AUTH_SOCK`.

---

### LOW — Quality

**DK-L1. Image is very large with all toolchains pre-installed**
`docker/brainbox/Dockerfile`
Homebrew, Go, Rust, Node (two versions), Python, AWS CLI, Azure CLI, Terraform, kubectl,
Playwright (Chromium), websocat, and several brew packages are all in a single image.
This is convenient but produces a large image (~5 GB+). Consider multi-stage builds or
role-specific images.

**DK-L2. Redundant stderr redirect in Dockerfile**
`Formula/brainbox.rb:86`
```bash
if ! docker info &> /dev/null 2>&1; then
```
`&>` already redirects stdout+stderr. The trailing `2>&1` is a no-op and misleading.

---

## 3. reflex/ — Plugin Structure, Skills, Scripts

### HIGH — Bugs / Security

**RF-H1. Guardrail regex patterns compiled on every hook invocation**
`scripts/guardrail.py:539`
`re.search(pattern.pattern, text, flags)` compiles the regex string fresh on every call.
With ~35 patterns checked on every PreToolUse hook, regex compilation happens hundreds of
times per session. Pre-compile all patterns into `re.Pattern` objects in `load_patterns`.

**RF-H2. ReDoS risk in rm pattern**
`scripts/guardrail.py:74`
```python
r"rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)*(/|/\*|…"
```
The outer `(...\s+)*` quantifier with the inner `[a-zA-Z]*` can cause catastrophic
backtracking on adversarially crafted input. Use an atomic group or rewrite as a
non-backtracking alternative (e.g. possessive quantifiers or a simpler structure).

**RF-H3. `notify.sh` — message injected into AppleScript string**
`scripts/notify.sh:38-40`
```bash
ESCAPED_MESSAGE=$(printf '%s' "$MESSAGE" | sed 's/\\/\\\\/g; s/"/\\"/g')
osascript -e "display notification \"${ESCAPED_MESSAGE}\" …"
```
The escaping only covers `\` and `"`. Other AppleScript-significant characters and
Unicode escapes are not handled. `MESSAGE` may come from Claude Code tool output which
could be attacker-influenced. Consider passing the message as a positional argument
to `osascript` rather than interpolating into the `-e` string.

**RF-H4. `network-guardrail.py` allowlist trusts any port on `host.docker.internal`**
`docker/brainbox/setup/network-guardrail.py:67`
```python
r"host[.]docker[.]internal:\d+",  # brainbox hub API (any port)
```
This allows an agent to POST/PUT/DELETE to any service running on the Docker host on
any port (e.g. a local database, admin APIs, development servers). Restrict to the
specific hub port:
```python
r"host[.]docker[.]internal:9999",
```

**RF-H5. `guardrail.py` — user-supplied regex in `additional_patterns` not validated**
`scripts/guardrail.py:466-475`
`guardrail-config.json` can define arbitrary additional patterns. No validation checks
whether the regex is syntactically valid or whether it could cause ReDoS. An invalid
regex would raise at hook time and cause `sys.exit(0)` (fail-open), silently disabling
guardrails.

---

### MEDIUM — Error Handling / Edge Cases

**RF-M1. `guardrail.py` SQL patterns only apply to `Bash` tool**
`scripts/guardrail.py:294-302`
`DELETE FROM`, `DROP TABLE`, `TRUNCATE` patterns specify `"tool": "Bash"`. SQL can also
be executed via MCP database tools (e.g. `mcp__sql-server__*`, `mcp__atlassian__*`).
Extend coverage to `"tool": "*"` or add separate `mcp__*` patterns.

**RF-M2. `guardrail-hook.sh` uses bare `python3`**
`scripts/guardrail-hook.sh:23`
`python3 "$SCRIPT_DIR/guardrail.py"` invokes whatever `python3` is on PATH. Inside
brainbox containers this is the uv-managed Python, but on bare macOS it may be missing
dependencies or be an old version. Use `uvx --python 3.12 python guardrail.py` or
explicitly specify the interpreter path.

**RF-M3. `ingest.py` creates a new Qdrant connection per file**
`scripts/ingest.py:610`
`ingest_to_qdrant` calls `connect_to_qdrant(qdrant_url)` for each file in a directory.
With many files, this creates and tears down many connections. Pass the client in from
`main()` and reuse it across files.

**RF-M4. `langfuse-trace.py` debug log has no file locking**
`scripts/langfuse-trace.py:65-67`
Multiple concurrent hook invocations (parallel tool calls) can interleave writes into
`langfuse-debug.log`. Use `fcntl.flock` or `portalocker` to serialise writes, or log
via the logging module which handles concurrent writes internally.

**RF-M5. `ingest.py` uses MD5 for deduplication point IDs**
`scripts/ingest.py:635,639`
`hashlib.md5()` is used to derive Qdrant point IDs. MD5 collisions (however unlikely in
practice) would silently overwrite existing points with different content. Use SHA-256
and truncate if a fixed-length ID is needed.

**RF-M6. `qdrant-websearch-store.py` collection name derived from env without sanitisation**
`scripts/qdrant-websearch-store.py:30-36`
```python
workspace_name = os.path.basename(workspace.rstrip("/")).lower()
return f"{workspace_name}_memories"
```
Qdrant collection names must match `[a-zA-Z0-9_-]+`. A workspace profile path with
special characters would produce an invalid collection name, causing a Qdrant API error.
Sanitise the workspace name before using it.

---

### LOW — Code Style / Quality

**RF-L1. `__pycache__` directories committed**
`reflex/plugins/reflex/scripts/__pycache__/`
Pre-compiled `.pyc` files are committed. Add `**/__pycache__/` and `*.pyc` to
`.gitignore`.

**RF-L2. `guardrail.py` — `Pattern` dataclass is mutable**
`scripts/guardrail.py:42`
`Pattern` is a regular dataclass; `severity` is mutated in-place for overrides
(`p.severity = Severity(overrides[p.name])`). This mutates the original list of
Pattern objects which is confusing. Use `dataclasses.replace(p, severity=…)` or make
the dataclass frozen and return a new instance.

**RF-L3. `langfuse-trace.py` — log path recomputed on every call**
`scripts/langfuse-trace.py:57-60`
`debug_log` constructs the log path from `CLAUDE_CONFIG_DIR` on every invocation.
Cache the path at module load time.

**RF-L4. `httpx.AsyncClient` falsely flagged in network-guardrail**
`docker/brainbox/setup/network-guardrail.py:43`
```python
r"httpx\.(post|put|delete|patch|AsyncClient)\s*\("
```
`httpx.AsyncClient(…)` creates a client object, not a write request. It will be blocked
as a false positive. Remove `AsyncClient` from the pattern.

---

## 4. Formula/ — Homebrew Formulas

### HIGH — Correctness

**FM-H1. `docker info` check has redundant stderr redirect**
`Formula/brainbox.rb:86`
```bash
if ! docker info &> /dev/null 2>&1; then
```
`&>` already combines stdout+stderr. The `2>&1` suffix is silently ignored by bash but
signals intent confusion and will confuse readers.

**FM-H2. Embedded compose uses `latest` image tags**
`Formula/brainbox.rb:22,56`
`ghcr.io/neverprepared/brainbox-api:latest`, `ghcr.io/neverprepared/brainbox-ui:latest`,
and `qdrant/qdrant:latest` are embedded in the formula. When a user runs
`brainbox pull` they may get image versions incompatible with the installed formula.
Pin images to the same version as the formula (`brainbox-api:v#{version}`).

---

### MEDIUM — Quality / Risk

**FM-M1. Embedded `docker-compose.yml` can drift from the repo copy**
`Formula/brainbox.rb:14-68`
The compose YAML is hardcoded inside the formula's `install` block rather than being
packaged in the release tarball. Any change to the compose configuration requires a
formula update. Consider packaging the compose file in the release tarball and
installing it with `(share/"brainbox").install "docker-compose.yml"`.

**FM-M2. No `brainbox restart` command**
`Formula/brainbox.rb:91-122`
The wrapper script handles `up/start/down/stop/logs/status/pull/version`. `restart` is
absent but commonly expected. Implement it as `down` followed by `up`.

**FM-M3. `reflex.rb` caveats show wrong `--plugin-dir` path**
`Formula/reflex.rb:16`
```ruby
claude --plugin-dir #{share}/reflex
```
The installed files are at `#{share}/reflex` (e.g. `/opt/homebrew/share/reflex`), but
the plugin expects `#{share}/reflex` to contain the `plugins/reflex/` structure.
The formula installs `Dir["plugins/reflex/*"]` into `share/"reflex"`, so the correct
flag would be `claude --plugin-dir #{share}` (one level up). Verify end-to-end.

**FM-M4. `shell-profiler.rb` missing `license` field**
`Formula/shell-profiler.rb`
All other formulas declare `license "MIT"`. `shell-profiler.rb` omits it, which will
trigger a Homebrew audit warning.

---

### LOW — Quality

**FM-L1. `brainbox.rb` help text says "API + Qdrant" but stack has 3 services**
`Formula/brainbox.rb:113`
```
echo "  up/start   Start brainbox stack (API + Qdrant)"
```
The compose file also starts `brainbox-ui`. Update to "Start brainbox stack (UI + API + Qdrant)".

---

## Summary Table

| ID | Package | Priority | Category | One-liner |
|----|---------|----------|----------|-----------|
| BB-H1 | brainbox | HIGH | Security | Session name not validated on /api/stop and /api/delete |
| BB-H2 | brainbox | HIGH | Correctness | /api/create returns 200 on failure |
| BB-H3 | brainbox | HIGH | Security | SSE endpoint /api/events unauthenticated |
| BB-H4 | brainbox | HIGH | Bug | inject_config_bundle chown fails on stopped container |
| BB-H5 | brainbox | HIGH | Bug | Race condition in port allocation |
| BB-H6 | brainbox | HIGH | Bug | Remote Docker client never closed — connection leak |
| BB-H7 | brainbox | HIGH | Bug | Secret written with trailing newline via echo |
| BB-H8 | brainbox | HIGH | Security | TOCTOU race in profile cache permission enforcement |
| BB-H9 | brainbox | HIGH | Security | _add_dir_translated follows symlinks — arbitrary file inclusion |
| BB-H10 | brainbox | HIGH | Security | /api/hub/agents endpoints unauthenticated |
| BB-H11 | brainbox | HIGH | Security | X-Forwarded-For spoofing bypasses rate limiting |
| BB-M1 | brainbox | MEDIUM | Bug | Fallback delete uses wrong container name |
| BB-M2 | brainbox | MEDIUM | Security | Hub state file written with default umask |
| BB-M3 | brainbox | MEDIUM | Security | write_secure_file not atomic |
| BB-M4 | brainbox | MEDIUM | Security | subprocess brew call PATH-dependent |
| BB-M5 | brainbox | MEDIUM | Bug | tmux prompt injection / working_dir unquoted |
| BB-M6 | brainbox | MEDIUM | Security | /exec gives authenticated clients arbitrary shell |
| BB-M7 | brainbox | MEDIUM | Bug | No size limit on profile cache env reads |
| BB-M8 | brainbox | MEDIUM | Bug | _find_available_port silently returns default on error |
| BB-L1 | brainbox | LOW | Quality | Duplicate session-listing logic |
| BB-L2 | brainbox | LOW | Quality | _ROLE_PREFIXES hardcoded |
| BB-L3 | brainbox | LOW | Quality | _translate path_map re-sorted per value |
| BB-L4 | brainbox | LOW | Quality | threading import comment drift |
| BB-L5 | brainbox | LOW | Quality | _now_ms/_iso_now defined at bottom of module |
| DK-H1 | docker | HIGH | Security | curl\|bash installers without checksum |
| DK-H2 | docker | HIGH | Security | Passwordless sudo for all commands |
| DK-H3 | docker | HIGH | Security | chmod -R 777 /ms-playwright |
| DK-H4 | docker | HIGH | Security | websocat from latest with no checksum |
| DK-H5 | docker | HIGH | Security | XDG_CONFIG_HOME mounted read-write |
| DK-H6 | docker | HIGH | Security | nginx forwards X-Forwarded-For → rate limit bypass |
| DK-H7 | docker | HIGH | Bug | HEALTHCHECK fails until Claude is manually launched |
| DK-M1 | docker | MEDIUM | Quality | mutable :latest image tags |
| DK-M2 | docker | MEDIUM | Security | nginx no client_max_body_size |
| DK-M3 | docker | MEDIUM | Security | nginx missing security headers |
| DK-M4 | docker | MEDIUM | Bug | SSH_AUTH_SOCK hardcoded macOS path |
| DK-L1 | docker | LOW | Quality | Very large monolithic image |
| DK-L2 | docker | LOW | Quality | Redundant 2>&1 after &> |
| RF-H1 | reflex | HIGH | Performance | Guardrail regex compiled every invocation |
| RF-H2 | reflex | HIGH | Security | ReDoS risk in rm pattern |
| RF-H3 | reflex | HIGH | Security | notify.sh — message injected into AppleScript |
| RF-H4 | reflex | HIGH | Security | network-guardrail allowlist too broad (any port) |
| RF-H5 | reflex | HIGH | Security | user-supplied additional_patterns not validated |
| RF-M1 | reflex | MEDIUM | Correctness | SQL guardrails only cover Bash tool |
| RF-M2 | reflex | MEDIUM | Bug | guardrail-hook.sh uses bare python3 |
| RF-M3 | reflex | MEDIUM | Performance | ingest.py creates new Qdrant connection per file |
| RF-M4 | reflex | MEDIUM | Bug | langfuse-trace.py debug log has no file locking |
| RF-M5 | reflex | MEDIUM | Correctness | MD5 used for Qdrant point IDs |
| RF-M6 | reflex | MEDIUM | Bug | websearch collection name not sanitised |
| RF-L1 | reflex | LOW | Quality | __pycache__ committed to git |
| RF-L2 | reflex | LOW | Quality | Pattern dataclass mutable — severity mutated in-place |
| RF-L3 | reflex | LOW | Quality | langfuse debug log path recomputed every call |
| RF-L4 | reflex | LOW | Quality | httpx.AsyncClient false-positive in network-guardrail |
| FM-H1 | Formula | HIGH | Correctness | docker info check redundant stderr redirect |
| FM-H2 | Formula | HIGH | Security | Embedded compose uses :latest image tags |
| FM-M1 | Formula | MEDIUM | Quality | Embedded compose can drift from repo copy |
| FM-M2 | Formula | MEDIUM | Quality | No brainbox restart command |
| FM-M3 | Formula | MEDIUM | Bug | reflex.rb caveats show wrong --plugin-dir path |
| FM-M4 | Formula | MEDIUM | Quality | shell-profiler.rb missing license field |
| FM-L1 | Formula | LOW | Quality | Help text says "API + Qdrant" but stack has 3 services |
