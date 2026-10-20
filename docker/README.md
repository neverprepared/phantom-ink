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

- Some services ship a `.env.example` - where one exists, copy it to `.env` before
  starting. OpenSearch needs no `.env`; its compose file is self-contained.
- Never commit `.env` files (they contain secrets)
- Data is persisted in Docker volumes
