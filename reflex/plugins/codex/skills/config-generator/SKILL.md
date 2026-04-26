---
name: config-generator
description: Generate infrastructure and CI/CD configuration files from intent. Use when the user needs Terraform, Helm charts, docker-compose, GitHub Actions, Kubernetes manifests, or other IaC/pipeline configs.
---

# Config Generator

You produce valid, deployable infrastructure and CI/CD configuration from natural language descriptions. Every file must pass its format's linter. No placeholder values — use sensible defaults and mark secrets with `${VAR}` references.

## Supported Targets

### Infrastructure as Code
| Target | Format | Validator |
|--------|--------|-----------|
| Terraform | `.tf` (HCL) | `terraform validate` |
| Helm | `Chart.yaml` + `templates/` | `helm lint` |
| Kubernetes | `.yaml` manifests | `kubectl apply --dry-run=client` |
| Docker Compose | `docker-compose.yml` | `docker compose config` |
| Pulumi | TypeScript/Python/Go | language linter |
| CloudFormation | `.yaml` | `cfn-lint` |

### CI/CD Pipelines
| Target | Format | Validator |
|--------|--------|-----------|
| GitHub Actions | `.github/workflows/*.yml` | `actionlint` |
| GitLab CI | `.gitlab-ci.yml` | `gitlab-ci-lint` |
| Azure Pipelines | `azure-pipelines.yml` | — |
| CircleCI | `.circleci/config.yml` | `circleci config validate` |
| Argo Workflows | `workflow.yaml` | `argo lint` |

## Execution Model

1. Identify the target platform and format
2. Determine environment: cloud provider, region, resource tier
3. Generate complete config — all files, all resources, all wiring
4. Print validation command

## Terraform Patterns

- **Provider blocks** with version constraints
- **Variables** in `variables.tf` with descriptions, types, and defaults
- **Outputs** in `outputs.tf` for all IDs, endpoints, and connection strings
- **Remote state** — S3/GCS backend block (commented, ready to uncomment)
- **Modules** — use for repeated patterns (e.g., multiple services)
- **Tags** — always include `Name`, `Environment`, `ManagedBy = "terraform"`
- **Data sources** — prefer `data` over hardcoded ARNs/IDs
- **Security groups** — least privilege, no `0.0.0.0/0` ingress unless explicitly asked

### Common Stacks

**ECS Fargate service:**
VPC (3-AZ) → ALB → ECS cluster → service → task definition → ECR repo → CloudWatch logs → IAM roles

**Lambda API:**
API Gateway v2 → Lambda function → IAM role → CloudWatch logs → DynamoDB/S3

**EKS cluster:**
VPC → EKS cluster → managed node groups → IRSA roles → aws-auth ConfigMap → addons (CoreDNS, kube-proxy, VPC CNI)

## Kubernetes Manifest Patterns

- **Deployment** with resource limits, health probes, pod disruption budget
- **Service** matching deployment labels
- **ConfigMap/Secret** for configuration
- **HPA** with CPU/memory targets
- **Ingress** with TLS if domain provided
- **NetworkPolicy** restricting ingress to required ports
- **ServiceAccount** with minimum RBAC

## GitHub Actions Patterns

- **Matrix builds** for multi-version/multi-OS
- **Caching** — language-appropriate (actions/cache or built-in)
- **Concurrency** — cancel in-progress runs on same branch
- **Permissions** — explicit, least privilege
- **Reusable workflows** for shared steps
- **Environment protection** for deploy jobs

```yaml
# Always include
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
permissions:
  contents: read
```

## Docker Compose Patterns

- **Multi-stage builds** referenced in `build.dockerfile`
- **Health checks** on every service
- **Named volumes** for persistence
- **Networks** separating frontend/backend/data tiers
- **Environment files** via `env_file`
- **Dependency ordering** with `depends_on.condition: service_healthy`

## Rules

- Never hardcode secrets — use `${VAR}` or secret manager references
- Always include `.env.example` listing every required variable
- Default to the smallest viable resource size (e.g., `t3.small`, `f1-micro`)
- Include comments explaining non-obvious configuration choices
- Generate `tfvars.example` for Terraform
- Use specific image tags, never `latest`

## Output Format

Write all files using apply_patch. Print the validation and apply commands at the end. For Terraform, print `terraform init && terraform plan`.
