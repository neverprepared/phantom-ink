# Brainbox Docker Image

Official Docker image for brainbox - sandboxed Claude Code session manager.

## Quick Start

```bash
# Pull the image
docker pull ghcr.io/neverprepared/brainbox:latest

# Run brainbox
docker run -it --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v ~/.config/developer:/home/developer/.config \
  ghcr.io/neverprepared/brainbox:latest \
  brainbox --help
```

## Installation via Homebrew (Recommended)

```bash
brew install neverprepared/phantom-ink/brainbox
brainbox --help
```

The Homebrew formula automatically handles Docker image pulling and provides a clean `brainbox` command.

## Building Locally

```bash
# Clone repository
git clone https://github.com/neverprepared/phantom-ink.git
cd phantom-ink

# Build image
just bb-docker-build

# Run
docker run -it --rm brainbox:latest brainbox --help
```

## Image Details

- **Base**: Ubuntu 24.04
- **User**: `developer` (non-root)
- **Python**: Managed via uv
- **Pre-installed**:
  - Claude Code CLI
  - Playwright MCP server
  - Docker client (for nested containers)
  - Common development tools

## Platforms

- `linux/amd64` (x86_64)
- `linux/arm64` (Apple Silicon, ARM servers)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BRAINBOX_IMAGE` | `brainbox:latest` | Override Docker image to use |
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` | `1` | Enable Claude Code Teams (injected automatically) |
| `BRAINBOX_HUB_URL` | — | Hub API URL for agent communication (injected automatically) |
| `BRAINBOX_URL` | — | Alias for `BRAINBOX_HUB_URL`; used by the brainbox MCP server (injected automatically) |
| `BRAINBOX_REPO_URL` | — | Associated repo URL for repo-specific agents (injected when `repo_url` is set on the task) |
| `BRAINBOX_TASK_ID` | — | ID of the task this agent is executing (injected when submitted via hub task) |
| `BRAINBOX_JOB_ID` | — | Job ID for grouping related tasks (injected when a job context exists) |

### Agent-injected env vars

`BRAINBOX_TASK_ID`, `BRAINBOX_JOB_ID`, and `BRAINBOX_REPO_URL` are injected by the container lifecycle when a task is submitted through the hub. Agents can use these to:

- Build unique branch names: `ratchet/${BRAINBOX_JOB_ID}-${BRAINBOX_TASK_ID}`
- Identify the repo to clone and target for PRs
- Report results back to the hub with the correct task context

## Volumes

| Volume | Purpose |
|--------|---------|
| `/var/run/docker.sock` | Docker socket for container orchestration |
| `~/.config/phantom-ink/brainbox` | Persistent configuration and session data |
| `/workspace` | Current working directory mount |

## Versions

Images are tagged with:
- `latest` - Latest stable release
- `X.Y.Z` - Specific version (e.g., `0.6.0`)
- `X.Y` - Minor version (e.g., `0.6`)

## Security

- Runs as non-root user (`developer`)
- No privileged mode required
- Docker socket mounted for container management only
- Secrets stored in `~/.config/developer/.secrets/` and injected into containers as `/home/developer/.env`

## Support

- **Issues**: https://github.com/neverprepared/phantom-ink/issues
- **Documentation**: https://github.com/neverprepared/phantom-ink/tree/main/brainbox
- **Releases**: https://github.com/neverprepared/phantom-ink/releases
