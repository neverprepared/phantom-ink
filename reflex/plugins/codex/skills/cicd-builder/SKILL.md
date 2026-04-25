---
name: cicd-builder
description: Generate CI/CD pipeline configurations from project descriptions. Use when the user needs GitHub Actions workflows, GitLab CI pipelines, Azure Pipelines, CircleCI configs, or ArgoCD application manifests.
---

# CI/CD Builder

You generate complete, working CI/CD pipeline configurations. Given a project description and deployment target, produce pipeline files that handle build, test, lint, security scanning, and deployment.

## Execution Model

1. Identify the CI/CD platform (default: GitHub Actions)
2. Determine the language, test framework, and deployment target
3. Generate complete pipeline config in one pass
4. Include all environments (dev, staging, production)
5. Print the validation command

## GitHub Actions (Primary)

### Standard CI Workflow
```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - # language-specific lint setup
      - run: # lint command

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        # language-specific version matrix
    steps:
      - uses: actions/checkout@v4
      - # setup + cache
      - run: # test command

  build:
    needs: [lint, test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - # build steps
      - uses: actions/upload-artifact@v4
        with:
          name: build-output
          path: dist/
```

### CD Workflow (Deploy)
```yaml
name: Deploy
on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      environment:
        type: choice
        options: [staging, production]
        default: staging

permissions:
  contents: read
  id-token: write  # OIDC for cloud auth

jobs:
  deploy-staging:
    if: github.event_name == 'push' || github.event.inputs.environment == 'staging'
    runs-on: ubuntu-latest
    environment:
      name: staging
      url: https://staging.example.com
    steps:
      - uses: actions/checkout@v4
      - # deploy to staging

  deploy-production:
    if: github.event.inputs.environment == 'production'
    needs: [deploy-staging]
    runs-on: ubuntu-latest
    environment:
      name: production
      url: https://example.com
    steps:
      - uses: actions/checkout@v4
      - # deploy to production
```

### Language-Specific Caching

**Node.js (pnpm)**
```yaml
- uses: pnpm/action-setup@v4
- uses: actions/setup-node@v4
  with:
    node-version: 22
    cache: pnpm
```

**Python (uv)**
```yaml
- uses: astral-sh/setup-uv@v4
  with:
    enable-cache: true
- run: uv sync --frozen
```

**Go**
```yaml
- uses: actions/setup-go@v5
  with:
    go-version-file: go.mod
    cache: true
```

**Rust**
```yaml
- uses: dtolnay/rust-toolchain@stable
- uses: Swatinem/rust-cache@v2
```

### Security Scanning Job
```yaml
  security:
    runs-on: ubuntu-latest
    permissions:
      security-events: write
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: ${{ matrix.language }}
      - uses: github/codeql-action/autobuild@v3
      - uses: github/codeql-action/analyze@v3
```

### Docker Build + Push
```yaml
  docker:
    needs: [lint, test]
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}
          tags: |
            ghcr.io/${{ github.repository }}:${{ github.sha }}
            ghcr.io/${{ github.repository }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

### Release Workflow
```yaml
name: Release
on:
  push:
    tags: ['v*']

permissions:
  contents: write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - # build release artifacts
      - uses: softprops/action-gh-release@v2
        with:
          generate_release_notes: true
          files: |
            dist/*
```

## GitLab CI

```yaml
stages:
  - lint
  - test
  - build
  - deploy

variables:
  DOCKER_TLS_CERTDIR: "/certs"

lint:
  stage: lint
  image: # language image
  script:
    - # lint commands
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

test:
  stage: test
  image: # language image
  script:
    - # test commands
  coverage: '/^TOTAL.*\s+(\d+\%)$/'
  artifacts:
    reports:
      junit: report.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml

build:
  stage: build
  image: docker:27
  services:
    - docker:27-dind
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

deploy:staging:
  stage: deploy
  environment:
    name: staging
    url: https://staging.example.com
  script:
    - # deploy commands
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
```

## Deployment Targets

### AWS ECS
```yaml
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
    aws-region: us-east-1
- uses: aws-actions/amazon-ecs-deploy-task-definition@v2
  with:
    task-definition: task-definition.json
    service: my-service
    cluster: my-cluster
    wait-for-service-stability: true
```

### Kubernetes
```yaml
- uses: azure/setup-kubectl@v4
- run: |
    kubectl set image deployment/$DEPLOYMENT $CONTAINER=ghcr.io/${{ github.repository }}:${{ github.sha }}
    kubectl rollout status deployment/$DEPLOYMENT --timeout=300s
```

### Vercel / Netlify / Cloudflare Pages
```yaml
- uses: amondnet/vercel-action@v25
  with:
    vercel-token: ${{ secrets.VERCEL_TOKEN }}
    vercel-org-id: ${{ secrets.VERCEL_ORG_ID }}
    vercel-project-id: ${{ secrets.VERCEL_PROJECT_ID }}
    vercel-args: ${{ github.ref == 'refs/heads/main' && '--prod' || '' }}
```

## Rules

- **Explicit permissions** — always declare, never rely on defaults
- **Pin action versions** — use `@v4` not `@main`
- **Concurrency control** — always set on PR workflows
- **Cache everything** — dependencies, build artifacts, Docker layers
- **Fail fast** — `continue-on-error: false` (default), no silent failures
- **Secrets via OIDC** — prefer `id-token: write` + role assumption over long-lived secrets
- **Matrix wisely** — only for genuinely different configurations (OS, version)
- **Artifact retention** — set `retention-days` to avoid storage bloat

## Output Format

Write all pipeline files using apply_patch. Print:
- Required repository secrets to configure
- Required environment configurations
- The validation command (e.g., `actionlint`)
