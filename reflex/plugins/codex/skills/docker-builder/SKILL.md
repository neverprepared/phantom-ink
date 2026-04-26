---
name: docker-builder
description: Generate optimized Dockerfiles, docker-compose configs, and container infrastructure from project descriptions. Use when the user needs to containerize an application, build multi-service stacks, or optimize existing container configs.
---

# Docker Builder

You generate production-grade container configurations. Given a project or service description, produce optimized Dockerfiles, compose files, and supporting scripts.

## Execution Model

1. Identify the application language, framework, and dependencies
2. Determine if single container or multi-service stack
3. Generate all container config files in one pass
4. Print build and run commands

## Dockerfile Patterns

### Multi-Stage Build (Always Use)
```dockerfile
# Stage 1: Build
FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

# Stage 2: Production
FROM node:22-alpine AS runtime
RUN addgroup -g 1001 appgroup && adduser -u 1001 -G appgroup -D appuser
WORKDIR /app
COPY --from=builder --chown=appuser:appgroup /app/dist ./dist
COPY --from=builder --chown=appuser:appgroup /app/node_modules ./node_modules
COPY --from=builder --chown=appuser:appgroup /app/package.json ./
USER appuser
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://localhost:3000/health || exit 1
CMD ["node", "dist/index.js"]
```

### Language-Specific Base Images

| Language | Build Stage | Runtime Stage |
|----------|-------------|---------------|
| Node.js | `node:22-alpine` | `node:22-alpine` |
| Python | `python:3.13-slim` | `python:3.13-slim` |
| Go | `golang:1.24-alpine` | `gcr.io/distroless/static-debian12` |
| Rust | `rust:1.82-alpine` | `gcr.io/distroless/cc-debian12` |
| Java | `eclipse-temurin:21-jdk-alpine` | `eclipse-temurin:21-jre-alpine` |
| .NET | `mcr.microsoft.com/dotnet/sdk:9.0-alpine` | `mcr.microsoft.com/dotnet/aspnet:9.0-alpine` |

### Go — Scratch/Distroless
```dockerfile
FROM golang:1.24-alpine AS builder
RUN apk add --no-cache git ca-certificates
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app/server ./cmd/server

FROM gcr.io/distroless/static-debian12
COPY --from=builder /app/server /server
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/server"]
```

### Python — UV
```dockerfile
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable
COPY . .

FROM python:3.13-slim
RUN groupadd -r app && useradd -r -g app -d /app app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH"
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Docker Compose Patterns

### Standard Stack
```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
      target: runtime
    ports:
      - "${APP_PORT:-3000}:3000"
    environment:
      - DATABASE_URL=postgresql://app:${DB_PASSWORD}@db:5432/appdb
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3000/health"]
      interval: 30s
      timeout: 3s
      retries: 3

  db:
    image: postgres:17-alpine
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    environment:
      POSTGRES_DB: appdb
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d appdb"]
      interval: 10s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru

volumes:
  pgdata:
  redisdata:
```

## Optimization Checklist

Every generated Dockerfile must follow:

- [ ] **Multi-stage build** — separate build and runtime stages
- [ ] **Minimal base image** — alpine, slim, or distroless
- [ ] **Non-root user** — create and switch to unprivileged user
- [ ] **Layer caching** — COPY dependency manifests before source code
- [ ] **No unnecessary tools** — don't install curl/wget in prod unless needed for healthcheck
- [ ] **.dockerignore** — always generate alongside Dockerfile
- [ ] **HEALTHCHECK** — on every service
- [ ] **Specific tags** — never use `:latest`
- [ ] **LABEL metadata** — `org.opencontainers.image.*` labels
- [ ] **Signal handling** — use `exec` form CMD, or `tini` as init
- [ ] **Read-only rootfs** — use `read_only: true` in compose where possible
- [ ] **Resource limits** — `deploy.resources.limits` in compose

## .dockerignore (Always Generate)
```
.git
.github
.vscode
node_modules
dist
build
*.md
!README.md
.env
.env.*
*.log
__pycache__
.pytest_cache
.mypy_cache
*.pyc
coverage
.coverage
```

## Output Format

Write all files using apply_patch. Always generate:
1. `Dockerfile` (or `Dockerfile.{service}` for multi-service)
2. `.dockerignore`
3. `docker-compose.yml` (if multi-service)
4. `.env.example` with all required variables

Print build and run commands at the end.
