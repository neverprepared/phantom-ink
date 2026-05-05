# Docker

This directory contains Docker images and Compose configurations for the phantom-ink platform.

## Available Services

| Directory | Service | Purpose |
|-----------|---------|---------|
| `brainbox/` | Brainbox | Container image for sandboxed Claude Code sessions |
| `langfuse/` | LangFuse | LLM observability and tracing |
| `minio/` | MinIO | S3-compatible object storage for artifacts |

## Quick Start

### LangFuse (Optional — for observability)

```bash
cd langfuse
cp .env.example .env
# Edit .env and generate secrets (see comments in file)
docker compose up -d
```

Web UI: http://localhost:3000

## Notes

- Each service has its own `.env.example` file - copy to `.env` before starting
- Never commit `.env` files (they contain secrets)
- Data is persisted in Docker volumes
