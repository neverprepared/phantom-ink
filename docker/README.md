# Docker

This directory contains Docker images and Compose configurations for the phantom-ink platform.

## Available Services

| Directory | Service | Purpose |
|-----------|---------|---------|
| `brainbox/` | Brainbox | Container image for sandboxed Claude Code sessions |
| `minio/` | MinIO | S3-compatible object storage for artifacts |
| `opensearch/` | OpenSearch | Observability — logs, metrics and traces |

## Quick Start

### OpenSearch (observability)

```bash
cd opensearch
docker compose up -d
```

Or from the repo root: `just opensearch-start`.

## Notes

- Each service has its own `.env.example` file - copy to `.env` before starting
- Never commit `.env` files (they contain secrets)
- Data is persisted in Docker volumes
