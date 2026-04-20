---
name: cloud-inventory
description: Multi-cloud resource graph exploration, indexing, and diagram generation for AWS, Azure, and GCP
---

# Cloud Inventory Skill

## When to Use

- Exploring cloud infrastructure relationships starting from any resource
- Building or refreshing the local resource index for AWS, Azure, or GCP
- Generating infrastructure diagrams (Graphviz or Mermaid) from live cloud state
- Looking up resource metadata by name, ID, or tag without opening the cloud console

> Explore cloud infrastructure as a graph — start from any resource, walk its relationships, store the index locally, and generate diagrams on demand.

## Overview

This skill drives the `/reflex:inventory` command. The system uses a SQLite index at `$REFLEX_HOME/inventory.db` as the source of truth. Cloud APIs are only queried to resolve unknowns or refresh stale entries — the index is the fast path.

Capabilities:
- Resolve resources by name, native ID, or tag/type filter
- Walk resource relationships N levels deep using native CLIs
- Store discovered resources and edges in a local SQLite graph
- Generate Graphviz DOT (large graphs) or Mermaid (small graphs) diagrams
- Diff index state between scans

## Prerequisites

```bash
# AWS
brew install awscli
aws configure    # or set AWS_PROFILE

# Azure
brew install azure-cli
az login

# GCP
brew install --cask google-cloud-sdk
gcloud auth login
gcloud auth application-default login
```

## Configuration

```bash
# Override the database directory (default: $HOME/.config/reflex)
export REFLEX_HOME=/path/to/data

# AWS
export AWS_PROFILE=prod
export AWS_DEFAULT_REGION=us-east-1

# Azure
export AZURE_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# GCP
export GCLOUD_PROJECT=my-gcp-project
```

## Resolution: Finding a Starting Resource

The inventory resolves resources in this order:

1. **Exact native ID match** in the local index
2. **FTS full-text search** on name, resource_id, service, resource_type
3. **Glob name match** (`*prod*` → SQL `LIKE %prod%`)
4. **Live CLI query** (if not found in index and `--provider` is specified)

### Filter syntax

| Expression | Meaning |
|------------|---------|
| `tag:env=prod` | Resource has tag key `env` with value `prod` |
| `type:vpc` | Service/resource type is `vpc` |
| `name:*prod*` | Name glob match |

### Live resolution per provider

```bash
# AWS — name tag lookup via Resource Groups Tagging API
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Name,Values=prod-vpc \
  --output json

# Azure — resource search by name
az resource list --name prod-vnet --output json

# GCP — asset search
gcloud asset search-all-resources --query="name:prod-network" --format=json
```

## Relationship Maps

Each provider defines which related resources to fetch for each service type.
The traversal engine walks these rules from the root resource outward.

### AWS

| Source service | Related resources fetched |
|---------------|--------------------------|
| `vpc` | subnets, route-tables, security-groups, internet-gateways, NAT gateways, VPC peering |
| `subnet` | EC2 instances, RDS instances |
| `ec2` | EBS volumes, network interfaces |
| `alb` | target groups, listeners |
| `lambda` | function configuration |

#### Key CLI patterns

```bash
# VPC → subnets
aws ec2 describe-subnets \
  --filters Name=vpc-id,Values=vpc-0abc123 \
  --output json

# VPC → security groups
aws ec2 describe-security-groups \
  --filters Name=vpc-id,Values=vpc-0abc123 \
  --output json

# VPC → internet gateways
aws ec2 describe-internet-gateways \
  --filters Name=attachment.vpc-id,Values=vpc-0abc123 \
  --output json

# VPC → NAT gateways
aws ec2 describe-nat-gateways \
  --filter Name=vpc-id,Values=vpc-0abc123 \
  --output json

# Subnet → EC2 instances
aws ec2 describe-instances \
  --filters Name=subnet-id,Values=subnet-0abc123 \
  --output json

# EC2 → EBS volumes
aws ec2 describe-volumes \
  --filters Name=attachment.instance-id,Values=i-0abc123 \
  --output json

# ALB → target groups
aws elbv2 describe-target-groups \
  --load-balancer-arn arn:aws:elasticloadbalancing:... \
  --output json
```

#### AWS resource ID formats

| Service | ID field | Example |
|---------|----------|---------|
| VPC | `VpcId` | `vpc-0abc123def456789a` |
| Subnet | `SubnetId` | `subnet-0abc123def456789a` |
| EC2 | `InstanceId` | `i-0abc123def456789a` |
| Security Group | `GroupId` | `sg-0abc123def456789a` |
| Route Table | `RouteTableId` | `rtb-0abc123def456789a` |
| Internet Gateway | `InternetGatewayId` | `igw-0abc123def456789a` |
| NAT Gateway | `NatGatewayId` | `nat-0abc123def456789a` |

Name tags live in `Tags[?Key=='Name']|[0].Value` — always check this for human-readable names.

---

### Azure

| Source service | Related resources fetched |
|---------------|--------------------------|
| `resource-group` | All resources in group |
| `vnet` | Subnets, VNet peering |
| `subnet` | VMs, App Services |
| `vm` | VM detail, managed disks |
| `aks` | Cluster detail |
| `nsg` | NSG rules detail |

#### Key CLI patterns

```bash
# Resource group → all resources
az resource list --resource-group prod-rg --output json

# VNet → subnets
az network vnet subnet list \
  --vnet-name prod-vnet \
  --resource-group prod-rg \
  --output json

# VNet → peering
az network vnet peering list \
  --vnet-name prod-vnet \
  --resource-group prod-rg \
  --output json

# VM → NIC details (leads to subnet + NSG)
az vm show \
  --name prod-vm \
  --resource-group prod-rg \
  --output json

# Disk list for resource group
az disk list \
  --resource-group prod-rg \
  --output json

# AKS cluster details
az aks show \
  --name prod-aks \
  --resource-group prod-rg \
  --output json

# NSG rules
az network nsg show \
  --name prod-nsg \
  --resource-group prod-rg \
  --output json
```

#### Extracting resource-group from Azure resource ID

Azure resource IDs follow:
```
/subscriptions/{sub}/resourceGroups/{rg}/providers/{ns}/{type}/{name}
```

Split on `/` and find the index after `resourceGroups` to extract the RG.

---

### GCP

| Source service | Related resources fetched |
|---------------|--------------------------|
| `vpc-network` | Subnets, firewall rules, Cloud Routers |
| `subnet` | VM instances |
| `vm` | VM details |
| `gke-cluster` | Cluster details |

#### Key CLI patterns

```bash
# VPC → subnets
gcloud compute networks subnets list \
  --filter="network~prod-network" \
  --format=json

# VPC → firewall rules
gcloud compute firewall-rules list \
  --filter="network~prod-network" \
  --format=json

# VPC → routers
gcloud compute routers list \
  --filter="network~prod-network" \
  --format=json

# Subnet → VM instances
gcloud compute instances list \
  --filter="networkInterfaces.subnetwork~prod-subnet" \
  --format=json

# VM details
gcloud compute instances describe prod-vm \
  --zone=us-central1-a \
  --format=json

# GKE cluster details
gcloud container clusters describe prod-cluster \
  --zone=us-central1-a \
  --format=json
```

#### GCP region extraction

GCP zone/region is embedded in the `zone` or `region` field:
- `zone`: `us-central1-a` → region: `us-central1`
- `region`: full URL ending in `/us-central1`

Extract with: `zone.split("/")[-1].rsplit("-", 1)[0]`

---

## Compound Resource IDs

The inventory uses a stable compound key to avoid duplicates across rescans:

```
{provider}:{account}:{region}:{service}:{resource_id}
```

Examples:
```
aws:123456789012:us-east-1:vpc:vpc-0abc123def456789a
azure:sub-uuid:eastus:vnet:prod-vnet
gcp:my-project:us-central1:vpc-network:prod-network
```

- `account` = AWS account ID / Azure subscription ID / GCP project
- `region` = normalized to lowercase; `global` for region-agnostic resources

---

## Diagram Generation

### Format selection (auto)

| Node count | Format |
|------------|--------|
| ≤ 15 nodes | Mermaid (renders in GitHub, VS Code, Obsidian) |
| > 15 nodes | Graphviz DOT (render with `dot -Tsvg`, `-Tpng`, or `-Tpdf`) |

Both formats can be forced with `--format mermaid` or `--format dot`.

### Rendering Graphviz DOT

```bash
# Render to SVG
dot -Tsvg inventory-diagram.dot -o inventory-diagram.svg

# Render to PNG
dot -Tpng inventory-diagram.dot -o inventory-diagram.png

# Open in browser (macOS)
dot -Tsvg inventory-diagram.dot | open -f -a Safari
```

### Color legend (service types)

| Color | Services |
|-------|----------|
| Blue `#AED6F1` | VPC, VNet, vpc-network |
| Green `#D5E8D4` | Subnet, S3, GKE |
| Orange `#FFE6CC` | Security Group, NSG, Firewall, NAT Gateway |
| Light Blue `#DAE8FC` | EC2, VM, Lambda |
| Purple `#E1D5E7` | RDS |
| Yellow `#FFF2CC` | Lambda |
| Pink `#F8CECC` | ALB |
| Grey `#F5F5F5` | Internet Gateway, Resource Group |

The root resource (starting point) is always rendered with a **bold border**.

---

## Extending the Relationship Map

To add new resource types, add entries to the `RULES` dict in `inventory.py`:

```python
AWS_RULES["ecs-cluster"] = [
    RelRule(
        cmd="aws ecs list-services --cluster {id} --output json",
        service="ecs-service",
        resource_type="service",
        rel_type="contains",
        list_key="serviceArns",
        id_field="",          # list of ARNs, not dicts
        name_field="",
    ),
]
```

For parsers that return flat lists of ARNs/IDs (not objects), add a custom handler in `explore_resource`.

---

## Tips

- **Start broad**: Use `resource-group` (Azure) or `vpc` (AWS/GCP) as the starting resource for the most connected subgraph.
- **Depth tuning**: `--depth 1` gives immediate neighbors only; `--depth 3` can be slow for large VPCs. Default is 2.
- **Stale index**: Run `/reflex:inventory refresh <resource>` to re-walk and upsert. The previous relationships are cleared before re-traversal.
- **Multi-account AWS**: Set `AWS_PROFILE` before exploring to scope queries to a specific account. Each account's resources get their own compound ID prefix.
- **azure-discover migration**: `/reflex:azure-discover` now delegates to this system. Existing traces stored in Qdrant remain accessible; new traces are also stored in `inventory.db`.
