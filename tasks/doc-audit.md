# Documentation Audit

## Files Reviewed

- `/home/developer/workspace/ink-bunny/CLAUDE.md`
- `/home/developer/workspace/ink-bunny/README.md`
- `/home/developer/workspace/ink-bunny/brainbox/README.md`
- `/home/developer/workspace/ink-bunny/brainbox/INSTALL.md`
- `/home/developer/workspace/ink-bunny/brainbox/docs/daemon.md`
- `/home/developer/workspace/ink-bunny/docker/README.md`
- `/home/developer/workspace/ink-bunny/docker/brainbox/README.md`
- `/home/developer/workspace/ink-bunny/reflex/README.md`
- `/home/developer/workspace/ink-bunny/reflex/CLAUDE.md`
- `/home/developer/workspace/ink-bunny/reflex/plugins/reflex/README.md`
- `/home/developer/workspace/ink-bunny/reflex/plugins/reflex/CLAUDE.md`
- `/home/developer/workspace/ink-bunny/reflex/plugins/reflex/.claude-plugin/plugin.json`
- `/home/developer/workspace/ink-bunny/reflex/plugins/reflex/mcp-catalog.json`
- `/home/developer/workspace/ink-bunny/reflex/plugins/reflex/hooks/hooks.json`
- `/home/developer/workspace/ink-bunny/reflex/plugins/reflex/commands/*.md` (19 files)
- `/home/developer/workspace/ink-bunny/shell-profiler/README.md`
- `/home/developer/workspace/ink-bunny/shell-profiler/CLAUDE.md`
- `/home/developer/workspace/ink-bunny/justfile`
- `/home/developer/workspace/ink-bunny/brainbox/src/brainbox/__main__.py`
- `/home/developer/workspace/ink-bunny/brainbox/src/brainbox/config.py`
- `/home/developer/workspace/ink-bunny/brainbox/src/brainbox/lifecycle.py`
- `/home/developer/workspace/ink-bunny/brainbox/src/brainbox/api.py`
- `/home/developer/workspace/ink-bunny/brainbox/scripts/run.sh`
- `/home/developer/workspace/ink-bunny/brainbox/pyproject.toml`
- `/home/developer/workspace/ink-bunny/docker/brainbox/Dockerfile`
- `/home/developer/workspace/ink-bunny/docker/brainbox/setup/` (directory listing)
- `/home/developer/workspace/ink-bunny/shell-profiler/go.mod`
- `/home/developer/workspace/ink-bunny/shell-profiler/internal/config/config.go`
- `/home/developer/workspace/ink-bunny/shell-profiler/Formula/shell-profiler.rb`

## Findings

---

### [SEVERITY: HIGH] brainbox config directory documented as `~/.config/brainbox/` — actual is `~/.config/developer/`

- **File**: `brainbox/INSTALL.md`
- **Line**: 127–130
- **Issue**: The Configuration section states "Brainbox stores configuration in `~/.config/brainbox/`" with subdirectories `sessions/` and files `secrets.env` and `config.yaml`. The actual config directory (from `brainbox/src/brainbox/config.py:_default_config_dir`) is `~/.config/developer` (or `$XDG_CONFIG_HOME/developer`). Additionally, `config.yaml` does not exist — brainbox uses pydantic-settings via environment variables for configuration, not a yaml file. Secrets live in a `.secrets/` subdirectory (`config.py:secrets_dir`), not a flat `secrets.env` file.
- **Suggested Fix**:
  ```
  Brainbox stores configuration in `~/.config/developer/` (or `$XDG_CONFIG_HOME/developer/`):
  - `sessions/` — Session state files
  - `.secrets/` — Resolved secret files
  - `logs/brainbox.log` — API server log
  - `brainbox.pid` — Daemon PID file

  Configuration is controlled via environment variables (see `brainbox/src/brainbox/config.py`).
  ```

---

### [SEVERITY: HIGH] docker/brainbox README documents wrong secrets path

- **File**: `docker/brainbox/README.md`
- **Line**: 84
- **Issue**: "Secrets stored in `~/.config/brainbox/secrets.env`" — wrong on both counts. The config directory is `~/.config/developer/` (not `~/.config/brainbox/`), and secrets are stored in the `.secrets/` subdirectory, not a flat `secrets.env` file. Secrets are ultimately injected into containers as `/home/developer/.env` (confirmed by `docker/brainbox/Dockerfile:186` setting `BASH_ENV=/home/developer/.env` and `brainbox/src/brainbox/backends/docker.py:190–198`).
- **Suggested Fix**: `Secrets stored in `~/.config/developer/.secrets/` and injected into containers as `/home/developer/.env``

---

### [SEVERITY: HIGH] shell-profiler README has wrong Homebrew tap name

- **File**: `shell-profiler/README.md`
- **Line**: 14
- **Issue**: `brew install neverprepared/shell-profiler/shell-profiler` uses tap `neverprepared/shell-profiler` which does not exist. The actual Homebrew tap (per `CLAUDE.md:139` and the formula at `shell-profiler/Formula/shell-profiler.rb`) is the consolidated `neverprepared/ink-bunny`.
- **Suggested Fix**: `brew install neverprepared/ink-bunny/shell-profiler`

---

### [SEVERITY: HIGH] shell-profiler README references wrong repository URL

- **File**: `shell-profiler/README.md`
- **Lines**: 3–5 (badge URLs) and 19–20 (From Source section)
- **Issue**: CI/Release badges point to `https://github.com/neverprepared/shell-profile-manager` (a standalone repo that no longer holds this code). The "From Source" section clones `https://github.com/neverprepared/shell-profiler.git`. Both are wrong — shell-profiler lives in the `neverprepared/ink-bunny` monorepo. The Go module path (`go.mod:1`) is `github.com/neverprepared/shell-profile-manager` which is now a stale module path.
- **Suggested Fix**: Update badge URLs to `https://github.com/neverprepared/ink-bunny`. Update From Source clone URL to `https://github.com/neverprepared/ink-bunny.git` with `cd ink-bunny/shell-profiler`.

---

### [SEVERITY: HIGH] shell-profiler CLAUDE.md has wrong config and profiles paths

- **File**: `shell-profiler/CLAUDE.md`
- **Line**: File Locations section (near end of file)
- **Issue**: Documents:
  - **Config**: `~/.config/shell-profiler/config.yaml` — WRONG. Actual config file is `~/.profile-manager` in the user's home directory, using a simple `key=value` format (not YAML). See `shell-profiler/internal/config/config.go:11` (`configFileName = ".profile-manager"`).
  - **Profiles**: `~/.config/shell-profiler/profiles/` — WRONG. Actual default is `~/workspaces/profiles`. See `config.go:124` (`ProfilesDir: filepath.Join(homeDir, "workspaces", "profiles")`).
- **Suggested Fix**:
  ```
  - **Config**: `~/.profile-manager` (key=value format, not YAML)
  - **Profiles**: `~/workspaces/profiles` (default; configurable via profiles_dir in config file)
  ```

---

### [SEVERITY: MEDIUM] Root CLAUDE.md references non-existent `docker/docker-compose.yml`

- **File**: `CLAUDE.md`
- **Line**: 10
- **Issue**: Lists `docker/docker-compose.yml (unified)` as a file within `docker/`. This file does not exist. Each service has its own compose file: `docker/langfuse/docker-compose.yml`, `docker/minio/docker-compose.yml`, `docker/qdrant/docker-compose.yml`. There is no unified top-level compose file at `docker/`.
- **Suggested Fix**: Remove `docker/docker-compose.yml (unified)` from the description. The description should note that each service subdirectory contains its own `docker-compose.yml`.

---

### [SEVERITY: MEDIUM] Root CLAUDE.md scripts documentation is incomplete

- **File**: `CLAUDE.md`
- **Line**: 35–36
- **Issue**: "Scripts in `reflex/plugins/reflex/scripts/` implement hooks and tooling: `guardrail.py` (destructive op blocking), `ingest.py` (Qdrant ingestion), `summarize.py` (transcript summarizer), `mcp-generate.sh` (MCP registration)." The actual scripts directory contains 16 files — 12 are undocumented: `brainbox-connect.sh`, `brainbox-hook.sh` (brainbox API startup check), `check-dependencies.sh` (session start dependency check), `guardrail-hook.sh` (shell wrapper for guardrail), `langfuse-hook.sh`, `langfuse-trace.py`, `notify-hook.sh`, `notify.sh`, `qdrant-websearch-hook.sh`, `qdrant-websearch-store.py`, `statusline.sh`. The hooks.json references `guardrail-hook.sh`, `langfuse-hook.sh`, etc. — not the Python scripts directly.
- **Suggested Fix**: Update scripts description to reflect the full set of scripts and their roles, noting that `.sh` scripts are the hook entry points and `.py` scripts are their implementations.

---

### [SEVERITY: MEDIUM] reflex/README.md has wrong skill and command counts

- **File**: `reflex/README.md`
- **Line**: 17–18
- **Issue**: The features table lists "Skills | 40" and "Commands | 15". Actual counts verified from directory listings:
  - Skills: **42** (`ls reflex/plugins/reflex/skills/ | wc -l` → 42)
  - Commands: **19** (`ls reflex/plugins/reflex/commands/ | wc -l` → 19)
- **Suggested Fix**: Update table to "Skills | 42" and "Commands | 19".

---

### [SEVERITY: MEDIUM] reflex/README.md structure diagram shows `docker/` inside `reflex/`

- **File**: `reflex/README.md`
- **Lines**: 48–58
- **Issue**: The Structure section shows:
  ```
  reflex/
  ├── plugins/reflex/
  └── docker/
      ├── qdrant/
      └── langfuse/
  ```
  The `docker/` directory is at the **monorepo root**, not inside `reflex/`. The `reflex/CLAUDE.md` (Docker Services section) correctly notes "Docker services live at the monorepo root (not inside reflex/)".
- **Suggested Fix**: Remove `docker/` from the reflex structure diagram or add a note that it is a sibling at `../docker/` relative to reflex/.

---

### [SEVERITY: MEDIUM] reflex plugin README says 15 MCP servers, table missing 2 entries

- **File**: `reflex/plugins/reflex/README.md`
- **Line**: 116 (count) and table at lines 119–135
- **Issue**: "Reflex includes a catalog of 15 MCP servers" — actual count is **17** (verified from `mcp-catalog.json`). The MCP server table is also missing two servers: `brainbox` (container session management) and `uptime-kuma` (monitoring).
- **Suggested Fix**: Update count to 17 and add rows for `brainbox` and `uptime-kuma` to the table.

---

### [SEVERITY: MEDIUM] reflex plugin README documents wrong Docker compose file location

- **File**: `reflex/plugins/reflex/README.md`
- **Line**: 104
- **Issue**: "Docker compose files are stored at `~/.claude/docker/`" — this is incorrect. Docker compose files live in the monorepo at `docker/qdrant/`, `docker/langfuse/`, and `docker/minio/`. They are not copied to or sourced from `~/.claude/docker/`.
- **Suggested Fix**: "Docker compose files are in the `docker/` directory at the monorepo root. Start them with `just reflex-qdrant` / `just reflex-langfuse` from the repo root, or `cd docker/qdrant && docker compose up -d`."

---

### [SEVERITY: MEDIUM] brainbox/docs/daemon.md "See Also" references non-existent files

- **File**: `brainbox/docs/daemon.md`
- **Lines**: 456–458
- **Issue**: The "See Also" section links to `api.md`, `configuration.md`, and `deployment.md` — none of these files exist in `brainbox/docs/`. The only files in that directory are `daemon.md`, `QUERY_API_PROTOTYPE.md`, `utm-setup.md`, `vind-migration.md`, and an `architecture/` subdirectory.
- **Suggested Fix**: Remove or update the See Also section to link to files that actually exist, or create stub files for the missing docs.

---

### [SEVERITY: MEDIUM] docker/README.md describes itself as "for Reflex" only

- **File**: `docker/README.md`
- **Line**: 1
- **Issue**: "This directory contains Docker Compose configurations for services used by Reflex." The `docker/` directory also contains the brainbox container image at `docker/brainbox/` (Dockerfile, Dockerfile.developer, Dockerfile.performer, Dockerfile.researcher, and setup files). The description excludes a major component.
- **Suggested Fix**: "This directory contains Docker images and Compose configurations for the ink-bunny platform: the brainbox container image (`brainbox/`) and supporting service stacks (Qdrant, LangFuse, MinIO)."

---

### [SEVERITY: MEDIUM] reflex plugin CLAUDE.md documents incomplete workflow subcommands

- **File**: `reflex/plugins/reflex/CLAUDE.md`
- **Line**: Commands table (workflow row)
- **Issue**: Documents `/reflex:workflow` as "Manage workflow templates (apply/list/create/sync/compose/status)" but the actual `commands/workflow.md` `argument-hint` field lists: `apply|list|create|edit|delete|sync|compose|status|variables|diff|steps`. Missing: `edit`, `delete`, `variables`, `diff`, `steps`.
- **Suggested Fix**: Update the command description to `(apply/list/create/edit/delete/sync/compose/status/variables/diff/steps)`.

---

### [SEVERITY: LOW] reflex plugin README documents on/off toggles for qdrant and langfuse that don't exist

- **File**: `reflex/plugins/reflex/README.md`
- **Lines**: 72–73
- **Issue**:
  - Line 72: `/reflex:qdrant <on|off|status>` — actual `commands/qdrant.md` only supports `[status]` (no on/off toggle; Qdrant connection is always on when MCP is registered).
  - Line 73: `/reflex:langfuse <on|off|status>` — actual `commands/langfuse.md` only supports `[status]` (tracing is always active when credentials are present, with no toggle).
- **Suggested Fix**:
  - `/reflex:qdrant [status]` — Show Qdrant connection status
  - `/reflex:langfuse [status]` — Show LangFuse observability status and configuration

---

### [SEVERITY: LOW] Root CLAUDE.md hooks documentation is incomplete

- **File**: `CLAUDE.md`
- **Line**: 33–34
- **Issue**: "hooks.json — hook configurations (guardrails, LangFuse, notifications)" understates what the hooks actually do. The real `hooks.json` configures:
  - **SessionStart**: `check-dependencies.sh` (dependency validation) + `brainbox-hook.sh` (brainbox API status)
  - **PreToolUse**: `guardrail-hook.sh` (destructive op blocking)
  - **PostToolUse**: `langfuse-hook.sh` + `qdrant-websearch-hook.sh` (auto-store search results) + `notify-hook.sh`
  The qdrant-websearch hook and the SessionStart hooks are not mentioned.
- **Suggested Fix**: Update to: "hook configurations (SessionStart: dependency check + brainbox status; PreToolUse: guardrails; PostToolUse: LangFuse tracing, Qdrant web-search auto-storage, notifications)"

---

### [SEVERITY: LOW] Root README.md describes "three tools" but monorepo has five packages

- **File**: `README.md`
- **Line**: 3
- **Issue**: "A monorepo for an agentic development platform with three tools" — the table only lists brainbox, reflex, and shell-profiler. The `docker/` package (container images and compose stacks) and `docs/` (architectural documentation) are omitted. CLAUDE.md correctly identifies five packages.
- **Suggested Fix**: Either update the count to five and add rows for docker/ and docs/, or add a note that docker/ and docs/ are supporting components rather than standalone tools.

---

### [SEVERITY: LOW] brainbox/INSTALL.md mentions non-existent config.yaml

- **File**: `brainbox/INSTALL.md`
- **Line**: 130
- **Issue**: Configuration section lists `config.yaml` as a file in `~/.config/brainbox/`. No such file exists. Brainbox reads all configuration through pydantic-settings (environment variables), not a YAML config file.
- **Suggested Fix**: Remove the `config.yaml` line entirely.
