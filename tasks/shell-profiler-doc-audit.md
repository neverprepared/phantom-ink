# Shell Profiler Documentation Audit

**Date**: 2026-02-23
**Scope**: All documentation files in `shell-profiler/`

---

## Summary

Nine documentation files were audited against the Go source code. Six categories of recurring errors were found and corrected across eight files. One file (`cmd/shell-profiler/README.md`) was completely rewritten due to total obsolescence.

---

## Errors Found and Fixed

### 1. Wrong binary name: `./profile` / `profile` instead of `shell-profiler`

**Source of truth**: `cmd/shell-profiler/main.go` — the binary is `shell-profiler`.

**Files fixed**:
- `cmd/shell-profiler/README.md` — full rewrite (see below)
- `docs/GETTING-STARTED.md` — all `./profile` occurrences replaced
- `docs/INSTALL.md` — all `./profile` / `profile` occurrences replaced
- `docs/QUICKSTART.md` — all `profile create` occurrences replaced
- `docs/PROJECT-SUMMARY.md` — all `profile create/list/delete/info/status` occurrences replaced

**Example**:
```
# Before
profile create my-project --template work

# After
shell-profiler create my-project --template work
```

---

### 2. Non-existent `dotfiles/` subdirectory in paths

**Source of truth**: `internal/commands/create.go` — creates `.gitconfig`, `.ssh/config`, `.aws/`, `.kube/`, `.config/`, etc. directly at the profile root. No `dotfiles/` subdirectory is created.

**Files fixed**:
- `README.md` — directory structure diagram and `.env` example paths
- `docs/GETTING-STARTED.md` — `vim dotfiles/.gitconfig` → `vim .gitconfig`, env var paths
- `docs/INSTALL.md` — `cat dotfiles/.gitconfig` → `cat .gitconfig`
- `docs/QUICKSTART.md` — all `dotfiles/.gitconfig`, `dotfiles/.docker`, `dotfiles/.kube/config`, `dotfiles/.config/nvim`, `dotfiles/.config/1Password/agent.toml` paths
- `docs/PROJECT-SUMMARY.md` — directory structure diagram and generated files list

**Example**:
```
# Before
GIT_CONFIG_GLOBAL="$WORKSPACE_HOME/dotfiles/.gitconfig"

# After
GIT_CONFIG_GLOBAL="$WORKSPACE_HOME/.gitconfig"
```

---

### 3. Wrong default profiles directory path

**Source of truth**: `internal/config/config.go` — default is `~/workspaces/profiles/`, not `~/.config/shell-profiler/profiles/`.

**Files fixed**:
- `docs/GETTING-STARTED.md` — `cd profiles/personal` → `cd ~/workspaces/profiles/personal`
- `docs/INSTALL.md` — `cd profiles/my-project` → `cd ~/workspaces/profiles/my-project`
- `docs/QUICKSTART.md` — `cd profiles/personal`, `cd profiles/work` → `cd ~/workspaces/profiles/...`
- `docs/PROJECT-SUMMARY.md` — Quick start and workflow examples
- `docs/template-architecture.md` — Data flow diagram file system path: `~/.config/shell-profiler/profiles/my-profile/` → `~/workspaces/profiles/my-profile/`
- `docs/envrc-system.md` — Workflow example: `cd ~/.config/shell-profiler/profiles/my-project` → `cd ~/workspaces/profiles/my-project`

---

### 4. Wrong `sync` subcommand name (`profile git` vs `shell-profiler sync`)

**Source of truth**: `internal/cli/app.go` — the git operation command is `sync` (not `git`).

**Files fixed**:
- `docs/PROJECT-SUMMARY.md` — management tools list: `shell-profiler git` → `shell-profiler sync`; file inventory CLI section likewise

---

### 5. Incorrect `.env` version control status

**Source of truth**: `internal/commands/create.go` — the generated `.gitignore` explicitly includes `.env`, making it gitignored by default.

**Files fixed**:
- `docs/envrc-system.md`:
  - Overview table: `.env` status changed from `version controlled` to `gitignored by default`
  - `.env` section Status field: `✅ Version controlled` → `⚠️ Gitignored by default`
  - File comparison table: `.env` Version Control column: `✅ Yes` → `⚠️ Gitignored by default`
  - Best practices: "Commit `.envrc` and `.env` to git" → "Commit `.envrc` to git (`.env` is gitignored by default)"

---

### 6. Wrong loading sequence description and `CreatedAt` format

**Source of truth**:
- `internal/templates/envrc.tpl` — step 3 loads `.global/exports.sh` from `$(dirname "$PWD")/.global/`, i.e., the parent directory of the profile directory.
- `internal/templates/templates.go` — `CreatedAt` format is `time.Now().UTC().Format("2006-01-02 15:04:05 UTC")`, not RFC3339.

**Files fixed**:
- `docs/envrc-system.md` — Loading sequence step 3: `global/.global/exports.sh` → `.global/exports.sh from the parent directory of the profile (if exists)`
- `internal/templates/README.md` — `CreatedAt` description: `(RFC3339 format)` → `(format: 2006-01-02 15:04:05 UTC)`

---

## File-by-File Changes

### `cmd/shell-profiler/README.md` — Complete rewrite

**Reason**: The file described an interim migration state where Go commands were gradually replacing shell scripts, with references to a non-existent `internal/scripts/` package and `profile.sh`. The migration is long complete. The file was entirely obsolete.

**New content**: Accurately reflects the fully-Go implementation with correct command list (`init`, `create/new/add`, `update/upgrade`, `list/ls`, `select/use`, `delete/remove/rm`, `restore`, `info/current/show`, `status`, `dotfiles`, `sync`), config file location (`~/.profile-manager`), and internal package structure.

---

### `README.md`

- Directory structure diagram: removed `dotfiles/` level, files now shown at profile root
- Quick start: `cd profiles/my-project` → `cd ~/workspaces/profiles/my-project`
- `.env` example: `GIT_CONFIG_GLOBAL="$WORKSPACE_HOME/dotfiles/.gitconfig"` → `"$WORKSPACE_HOME/.gitconfig"`
- Work/personal switching paths corrected

---

### `docs/GETTING-STARTED.md`

- All `./profile <cmd>` → `shell-profiler <cmd>` (create, list, info, status, delete, help)
- `vim dotfiles/.gitconfig` → `vim .gitconfig`
- `AZURE_CONFIG_DIR`, `CLOUDSDK_CONFIG`, `GIT_CONFIG_GLOBAL` paths: removed `dotfiles/` prefix
- Profile directory navigation: relative → absolute `~/workspaces/profiles/...`

---

### `docs/INSTALL.md`

- All `./profile create`, `profile list` etc. → `shell-profiler create`, `shell-profiler list` etc.
- `cat dotfiles/.gitconfig` → `cat .gitconfig`
- `cd profiles/my-project` → `cd ~/workspaces/profiles/my-project`
- `chmod +x profile` → `chmod +x shell-profiler`
- Shell alias examples: replaced `~/workspaces/build/workspace-profiles/profile list` with `shell-profiler list`
- `./profile delete <profile-name>` → `shell-profiler delete <profile-name>`
- `rm -rf profiles/` → `rm -rf ~/workspaces/profiles/`
- Help reference: `profile help` / `profile create --help` → `shell-profiler help` / `shell-profiler create --help`

---

### `docs/QUICKSTART.md`

- All `profile create` → `shell-profiler create`
- `cd profiles/personal`, `cd profiles/work`, `cd profiles/client-acme` → `cd ~/workspaces/profiles/...`
- `vim dotfiles/.gitconfig` → `vim .gitconfig`
- All `dotfiles/` path prefixes removed from `GIT_CONFIG_GLOBAL`, `DOCKER_CONFIG`, `KUBECONFIG`, `XDG_CONFIG_HOME`, `cp ~/.kube/config`, `mkdir -p .config/nvim`, `vim .config/1Password/agent.toml`
- XDG tool paths in prose: `dotfiles/.config/nvim/` → `.config/nvim/`, etc.
- Git config verification example path corrected

---

### `docs/PROJECT-SUMMARY.md`

- Architecture diagram: replaced incorrect `workspace-profiles/profile` binary reference; updated profile structure to show files at root (not `dotfiles/`)
- Profile structure description: `dotfiles/.gitconfig` → `.gitconfig`
- Management tools: added `select`, `update`, `dotfiles`; `profile git` → `shell-profiler sync`
- Workflow example: `./profile create`, `cd profiles/my-work-project` → `shell-profiler create`, `cd ~/workspaces/profiles/my-work-project`
- Quick start: `./profile create`, `cd profiles/my-project` → `shell-profiler create`, `cd ~/workspaces/profiles/my-project`
- Maintenance: `./profile list --verbose`, `./profile delete` → `shell-profiler list --verbose`, `shell-profiler delete`

---

### `docs/template-architecture.md`

- Data flow diagram: file system path corrected from `~/.config/shell-profiler/profiles/my-profile/` to `~/workspaces/profiles/my-profile/`

---

### `docs/envrc-system.md`

- Overview table: `.env` version control status corrected
- `.env` section Status field: corrected to gitignored
- Loading sequence step 3: corrected path and description
- File comparison table: `.env` column corrected
- Best practices: removed instruction to commit `.env`
- Workflow example: `cd ~/.config/shell-profiler/profiles/my-project` → `cd ~/workspaces/profiles/my-project`

---

### `internal/templates/README.md`

- `CreatedAt` format: `(RFC3339 format)` → `(format: 2006-01-02 15:04:05 UTC)`

---

## Files with No Changes Required

- `CLAUDE.md` — accurately describes config file at `~/.profile-manager`, correct profiles dir, correct internal package structure
- `docs/template-system.md` — content was accurate

---

## Second-Pass Errors Found and Fixed

A second audit pass against the Go source code found additional errors not corrected in the first pass.

### 7. Wrong `.envrc` loading description (vault discovery omitted)

**Source of truth**: `internal/templates/envrc.tpl` — the generated `.envrc` does NOT call `dotenv_if_exists .env` directly. It copies `.env` to a cache file at `$TMPDIR/sp-profiles/${WORKSPACE_PROFILE}/.env`, appends 1Password secrets from the `workspace-<profile>` vault, then loads the result via `dotenv_if_exists "$_sp_env"`.

**Files fixed**:
- `README.md` — `.envrc` bullet: `"Loads .env via dotenv_if_exists"` → description of vault discovery + caching
- `docs/PROJECT-SUMMARY.md` — envrc commands list: `dotenv_if_exists .env` → `dotenv_if_exists "$_sp_env"` with description

---

### 8. References to non-existent `docs/examples/` directory

**Source of truth**: The `docs/examples/` directory does not exist in the repository.

**Files fixed**:
- `README.md` — Contributing section: removed `and docs/examples/ for the current set of tool integrations`
- `README.md` — `.env` section: removed `See docs/examples/.env.example for the full list of supported variables.`
- `docs/INSTALL.md` — Quick start step 2: removed `cat docs/examples/.envrc.example` / `.gitconfig.example` block (renumbered subsequent steps)
- `docs/INSTALL.md` — Getting Help: removed `Check docs/examples/ directory` bullet
- `docs/QUICKSTART.md` — Next Steps: replaced `Look in docs/examples/ directory` with reference to editing profile config files directly
- `docs/PROJECT-SUMMARY.md` — removed entire "Templates (2 files)" subsection listing `docs/examples/.envrc.example` and `docs/examples/.gitconfig.example`

---

### 9. Remaining `dotfiles/` path prefixes

**Source of truth**: `internal/commands/create.go` — all files created at profile root, no `dotfiles/` subdirectory.

**Files fixed**:
- `docs/GETTING-STARTED.md` — `.env` example: `GIT_CONFIG_GLOBAL`, `DOCKER_CONFIG`, `KUBECONFIG` paths still had `dotfiles/` prefix
- `docs/INSTALL.md` — `use_profile` direnvrc helper: `GIT_CONFIG_GLOBAL="$WORKSPACE_HOME/dotfiles/.gitconfig"` → `"$WORKSPACE_HOME/.gitconfig"`
- `docs/PROJECT-SUMMARY.md` — Generated files list: `dotfiles/.gitconfig` → `.gitconfig`

---

### 10. Remaining bare `profiles/` navigation paths

**Source of truth**: `internal/config/config.go` — default profiles dir is `~/workspaces/profiles/`.

**Files fixed**:
- `docs/GETTING-STARTED.md` — client switching example: `cd profiles/client-alpha` / `cd ../client-beta`; work/personal example: `cd profiles/work` / `cd ../../profiles/personal`
- `docs/QUICKSTART.md` — existing project example: `cd profiles/my-existing-project` (both occurrences)
- `docs/QUICKSTART.md` — direnv output example: `~/workspaces/build/workspace-profiles/profiles/personal/.envrc` → `~/workspaces/profiles/personal/.envrc`

---

### 11. Wrong shell alias paths in INSTALL.md

**Source of truth**: `internal/config/config.go` — profiles dir is `~/workspaces/profiles/`, not `~/workspaces/build/workspace-profiles/profiles/`.

**Files fixed**:
- `docs/INSTALL.md` — aliases: `alias wp='cd ~/workspaces/build/workspace-profiles'` → `'cd ~/workspaces/profiles'`; `wpwork` and `wppersonal` aliases likewise corrected

---

### 12. Wrong uninstall step 2 in INSTALL.md

**Source of truth**: `internal/config/config.go` — config file is `~/.profile-manager`; profiles directory is user-configurable. The old step 2 (`cd .. && rm -rf workspace-profiles/`) assumed a specific directory layout and a working directory that may not exist.

**Files fixed**:
- `docs/INSTALL.md` — Uninstall step 2: replaced `cd .. && rm -rf workspace-profiles/` with `rm -f ~/.profile-manager`

---

### 13. `shell-profiler create --interactive` missing required profile name

**Source of truth**: `internal/cli/app.go` — `create` requires a profile name; if `opts.ProfileName == ""` it returns an error. `--interactive` is not a flag.

**Files fixed**:
- `docs/INSTALL.md` — Next Steps item 3: `shell-profiler create --interactive` → `shell-profiler create my-profile`

---

### 14. Wrong template modification file in PROJECT-SUMMARY.md

**Source of truth**: Templates live in `internal/templates/*.tpl` with rendering logic in `internal/templates/templates.go`. `internal/commands/create.go` calls those functions but does not contain the template markup.

**Files fixed**:
- `docs/PROJECT-SUMMARY.md` — Profile Templates section: `modify the template logic in internal/commands/create.go` → `modify the template files in internal/templates/`

---

### 15. `{{.ProfileName}}` described as "only in comments" in envrc-system.md

**Source of truth**: `internal/templates/envrc.tpl` — `{{.ProfileName}}` appears in both comments and in `export WORKSPACE_PROFILE="{{.ProfileName}}"`.

**Files fixed**:
- `docs/envrc-system.md` — Template variables table: `Only in comments` → `In comments and in export WORKSPACE_PROFILE="{{.ProfileName}}"`

---

### 16. `.envrc update` described as "OVERWRITTEN" in envrc-system.md

**Source of truth**: `internal/commands/update.go` — `UpdateProfile` applies targeted in-place changes: `updateEnvrc()` removes stale tool exports and adds missing `dotenv_if_exists .env`; `updateEnvrcVaultDiscovery()` replaces old `op inject` block with vault discovery block. It does NOT regenerate the full template.

**Files fixed**:
- `docs/envrc-system.md` — `.envrc` metadata Updates field: `Overwritten by shell-profiler create/update` → `shell-profiler create overwrites; shell-profiler update makes targeted in-place modifications`
- `docs/envrc-system.md` — `shell-profiler update` table row: `OVERWRITTEN with latest template` → `TARGETED IN-PLACE MODIFICATIONS`
