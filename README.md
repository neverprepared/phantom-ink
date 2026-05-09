<p align="center">
  <img src="assets/phantom-ink.png" alt="phantom-ink" width="220">
</p>

# phantom-ink

A macOS desktop app (built with [Wails](https://wails.io)) for managing workspace profiles, orchestrating the [Brainbox](docs/brainbox/README.md) AI-agent infrastructure, and controlling local AI services via [Ollama](https://ollama.com).

## What it does

- **Workspace profiles** — browse, switch, create, and delete shell-profiler profiles backed by direnv. Each profile carries its own git identity, credentials, and tool configuration.
- **Brainbox integration** — connect to a running Brainbox API, inspect hub state, manage sessions and agents, and restart the service (Docker or Homebrew daemon).
- **Service management** — enable/disable local integrations (Qdrant, n8n, Langfuse, MinIO, …) with one click; track per-service health and URLs.
- **Ollama** — list installed models and query status of the local Ollama daemon.
- **API key management** — store a Brainbox API key, masked in the UI.

## Repository layout

```
app/            Wails desktop app (Go backend + Svelte/TS frontend)
shell-profiler/ CLI tool for workspace profile management
brainbox/       Brainbox API server source
docker/         Docker Compose stacks for local services
docs/           Architecture docs and Brainbox API reference
Formula/        Homebrew formulae
```

## Building the desktop app

**Requirements:**

- Go 1.25+
- Node.js 18+
- [Wails CLI](https://wails.io/docs/gettingstarted/installation): `go install github.com/wailsapp/wails/v2/cmd/wails@latest`

```bash
cd app

# Live-reload development server (hot reloads Go + frontend)
make dev

# Production build (universal macOS binary → app/build/bin/phantom-ink.app)
make build
```

## Building the shell-profiler CLI

```bash
cd shell-profiler
make build          # outputs ./shell-profiler binary
make install        # copies binary to workspace root
```

Or install via Homebrew:

```bash
brew tap neverprepared/phantom-ink
brew install neverprepared/phantom-ink/shell-profiler
```

## Key commands (shell-profiler)

| Command | Description |
|---|---|
| `shell-profiler create <name>` | Create a new workspace profile |
| `shell-profiler list` | List all profiles (interactive) |
| `shell-profiler select [name]` | Select a profile and get activation instructions |
| `shell-profiler info` | Show the currently active profile |
| `shell-profiler dotfiles list [name]` | List dotfiles in a profile |
| `shell-profiler dotfiles edit [name]` | Edit a dotfile interactively |
| `shell-profiler sync init <name>` | Initialize git in a profile |
| `shell-profiler sync pull/push <name>` | Pull/push profile to remote |
| `shell-profiler delete <name>` | Delete a profile |

See [`shell-profiler/README.md`](shell-profiler/README.md) for full documentation.

## Architecture overview

```
phantom-ink (Wails app)
├── Go backend  ── Wails runtime bindings
│   ├── app.go           startup, config, platform helpers
│   ├── app_profiles.go  shell-profiler profile CRUD
│   ├── app_services.go  local service enable/disable
│   ├── app_brainbox.go  Brainbox API proxy methods
│   ├── app_ollama.go    Ollama model management
│   └── brainbox/        HTTP client + SSE listener for Brainbox API
└── Svelte/TS frontend  (app/frontend/)
```

The app persists configuration and profile state in a local SQLite database (`~/.phantom-ink.db`).

## Contributing

Pull requests welcome. See `shell-profiler/CLAUDE.md` for development guidelines on the CLI tool.
