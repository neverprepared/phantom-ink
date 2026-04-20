---
name: cloud-cost-optimization
description: Cloud cost optimization patterns for AWS, GCP, and Azure. Use when analyzing cloud spend, identifying waste, surfacing rightsizing recommendations, or auditing resource utilization across cloud providers. Read-only — surfaces findings and recommendations only, makes no changes.
---

# Cloud Cost Optimization

## Overview

Cloud cost optimization patterns for AWS, GCP, and Azure.

## When to Use

- Auditing cloud spend for waste or idle resources across AWS, GCP, or Azure
- Generating rightsizing recommendations before a budget review
- Identifying untagged or unaccountable resources
- Producing a read-only cost report for stakeholder review — no infrastructure changes are made

Read-only patterns for identifying cost waste and surfacing optimization recommendations across AWS, GCP, and Azure. All commands are query-only. Findings are presented as recommendations for human review and action.

## Core Principles

1. **Measure first** — never optimize blind; pull cost reports before drawing conclusions
2. **Rightsize before reserving** — verify usage is stable before recommending committed spend
3. **Flag untagged resources** — untagged resources are unaccountable resources
4. **Surface, don't act** — this skill identifies opportunities; implementation is a separate decision

---

## AWS Cost Optimization

### Cost Explorer — Spend Analysis

```bash
# Monthly spend by service (last 30 days)
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '30 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE

# Daily spend trend (last 14 days) — useful for spotting anomalies
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '14 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity DAILY \
  --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE

# Untagged resource spend (missing CostCenter tag)
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '7 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity DAILY \
  --metrics BlendedCost \
  --filter '{"Tags":{"Key":"CostCenter","MatchOptions":["ABSENT"]}}'

# Spend by tag (e.g. by Team)
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '30 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=TAG,Key=Team
```

### EC2 — Rightsizing Recommendations

```bash
# Compute Optimizer rightsizing recommendations (overprovisioned instances)
aws compute-optimizer get-ec2-instance-recommendations \
  --filters Name=Finding,Values=Overprovisioned

# Compute Optimizer — all findings summary
aws compute-optimizer get-recommendation-summaries

# CPU utilization for a specific instance over 14 days
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=i-xxxxxxxxx \
  --start-time $(date -d '14 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 1209600 \
  --statistics Average,Maximum

# Memory utilization (requires CloudWatch agent)
aws cloudwatch get-metric-statistics \
  --namespace CWAgent \
  --metric-name mem_used_percent \
  --dimensions Name=InstanceId,Value=i-xxxxxxxxx \
  --start-time $(date -d '14 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 1209600 \
  --statistics Average,Maximum

# List stopped instances (candidates for termination if stopped > 7 days)
aws ec2 describe-instances \
  --filters Name=instance-state-name,Values=stopped \
  --query "Reservations[].Instances[].{ID:InstanceId,Type:InstanceType,Stopped:StateTransitionReason,Tags:Tags}" \
  --output table

# Unattached EBS volumes (paying for storage with nothing attached)
aws ec2 describe-volumes \
  --filters Name=status,Values=available \
  --query "Volumes[].{ID:VolumeId,Size:Size,Type:VolumeType,Created:CreateTime}" \
  --output table

# Unattached Elastic IPs
aws ec2 describe-addresses \
  --query "Addresses[?AssociationId==null].{IP:PublicIp,AllocationId:AllocationId}" \
  --output table
```

### Savings Plans & Reserved Instances

```bash
# Savings Plans purchase recommendations (1-year, no upfront)
aws savingsplans get-savings-plans-purchase-recommendation \
  --savings-plans-type COMPUTE_SP \
  --term-in-years ONE_YEAR \
  --payment-option NO_UPFRONT \
  --lookback-period-in-days THIRTY_DAYS

# Current RI utilization (flag if < 80%)
aws ce get-reservation-utilization \
  --time-period Start=$(date -d '30 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d)

# Current Savings Plans utilization (flag if < 80%)
aws ce get-savings-plans-utilization \
  --time-period Start=$(date -d '30 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d)

# RI coverage — what % of usage is covered by reservations
aws ce get-reservation-coverage \
  --time-period Start=$(date -d '30 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --group-by Type=DIMENSION,Key=SERVICE
```

### S3 — Cost Findings

```bash
# List buckets missing lifecycle policies (candidates for tiering recommendations)
aws s3api list-buckets --query "Buckets[].Name" --output text | \
  tr '\t' '\n' | while read bucket; do
    policy=$(aws s3api get-bucket-lifecycle-configuration --bucket "$bucket" 2>&1)
    if echo "$policy" | grep -q "NoSuchLifecycleConfiguration"; then
      echo "NO LIFECYCLE POLICY: $bucket"
    fi
  done

# List buckets and their storage classes (identify Standard buckets with old objects)
aws s3api list-buckets --query "Buckets[].Name" --output text | \
  tr '\t' '\n' | while read bucket; do
    aws s3api get-bucket-location --bucket "$bucket" \
      --query "{Bucket:'$bucket',Region:LocationConstraint}" --output table 2>/dev/null
  done

# Check for incomplete multipart uploads (ongoing storage cost)
aws s3api list-multipart-uploads --bucket my-bucket \
  --query "Uploads[?Initiated<='$(date -d '7 days ago' --iso-8601=seconds)'].{Key:Key,Initiated:Initiated,StorageClass:StorageClass}"
```

> **Recommendation template** — if a bucket has no lifecycle policy and contains objects older than 30 days, recommend applying Intelligent-Tiering or a tiered lifecycle (Standard → Nearline at 30d → Archive at 90d).

### Lambda — Cost Findings

```bash
# Lambda functions using x86 (recommend Graviton arm64 — ~20% cheaper)
aws lambda list-functions \
  --query "Functions[?Architectures[0]!='arm64'].{Name:FunctionName,Runtime:Runtime,Arch:Architectures,Memory:MemorySize}" \
  --output table

# Functions with high memory allocation — potential rightsizing candidates
aws lambda list-functions \
  --query "Functions[?MemorySize>`512`].{Name:FunctionName,Memory:MemorySize,Timeout:Timeout}" \
  --output table

# Invocation count and duration for a function (last 7 days)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Duration \
  --dimensions Name=FunctionName,Value=my-function \
  --start-time $(date -d '7 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 604800 \
  --statistics Average,Maximum,p99

# Throttles — if high, may be over-provisioned on concurrency limits
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Throttles \
  --dimensions Name=FunctionName,Value=my-function \
  --start-time $(date -d '7 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 604800 \
  --statistics Sum
```

> **Recommendation**: Use [Lambda Power Tuning](https://github.com/alexcasalboni/aws-lambda-power-tuning) to find the optimal memory setting. Functions on x86 with stable workloads are candidates for Graviton migration.

### EKS — Cost Findings

```bash
# Node utilization — identify underutilized nodes
kubectl top nodes

# Pods without CPU/memory requests (prevents accurate bin-packing and Karpenter rightsizing)
kubectl get pods -A -o json | \
  python3 -c "
import json, sys
pods = json.load(sys.stdin)['items']
for p in pods:
    for c in p['spec']['containers']:
        r = c.get('resources', {}).get('requests', {})
        if not r.get('cpu') or not r.get('memory'):
            print(p['metadata']['namespace'], p['metadata']['name'], c['name'])
"

# Pod resource requests vs actual usage (requires metrics-server)
kubectl top pods -A --sort-by=cpu

# Nodes running On-Demand that could use Spot
kubectl get nodes -o json | \
  python3 -c "
import json, sys
nodes = json.load(sys.stdin)['items']
for n in nodes:
    labels = n['metadata'].get('labels', {})
    cap_type = labels.get('karpenter.sh/capacity-type', labels.get('eks.amazonaws.com/capacityType', 'unknown'))
    instance = labels.get('node.kubernetes.io/instance-type', 'unknown')
    name = n['metadata']['name']
    print(f'{name:50s}  {instance:20s}  {cap_type}')
"

# Check if Karpenter is installed (if not, cluster autoscaler may be over-provisioning)
kubectl get deployment -n karpenter karpenter 2>/dev/null || echo "Karpenter not installed"

# Check existing node pools / node groups
kubectl get nodepools 2>/dev/null || aws eks list-nodegroups --cluster-name my-cluster

# Kubecost cost breakdown by namespace (if Kubecost is installed)
kubectl get pods -n kubecost 2>/dev/null && \
  echo "Kubecost available — port-forward to localhost:9090 for cost breakdown"
```

> **Recommendations:**
> - Pods missing resource requests cannot be accurately bin-packed — flag to owning teams
> - On-Demand nodes running stateless workloads are candidates for Spot/Karpenter
> - If Karpenter is absent, evaluate it as a replacement for Cluster Autoscaler (better consolidation)
> - Namespace resource quotas prevent runaway spend — check if any namespaces are uncapped

### ECS — Cost Findings

```bash
# Check task CPU/memory utilization vs allocation (identify over-provisioned tasks)
aws cloudwatch get-metric-statistics \
  --namespace ECS/ContainerInsights \
  --metric-name CpuUtilized \
  --dimensions Name=ClusterName,Value=my-cluster Name=ServiceName,Value=my-service \
  --start-time $(date -d '7 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 604800 \
  --statistics Average,Maximum

aws cloudwatch get-metric-statistics \
  --namespace ECS/ContainerInsights \
  --metric-name MemoryUtilized \
  --dimensions Name=ClusterName,Value=my-cluster Name=ServiceName,Value=my-service \
  --start-time $(date -d '7 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 604800 \
  --statistics Average,Maximum

# List services and their capacity provider strategy (flag services not using Fargate Spot)
aws ecs list-services --cluster my-cluster --output text | \
  awk '{print $2}' | xargs -I{} aws ecs describe-services \
    --cluster my-cluster --services {} \
    --query "services[].{Name:serviceName,CapacityProviders:capacityProviderStrategy,Running:runningCount,Desired:desiredCount}" \
    --output table

# List task definitions and their CPU/memory allocation
aws ecs list-task-definitions --status ACTIVE --output text | \
  awk '{print $2}' | head -20 | xargs -I{} aws ecs describe-task-definition \
    --task-definition {} \
    --query "taskDefinition.{Family:family,CPU:cpu,Memory:memory}" \
    --output table

# Services with no running tasks (idle, paying for nothing or misconfigured)
aws ecs list-services --cluster my-cluster --output text | \
  awk '{print $2}' | xargs -I{} aws ecs describe-services \
    --cluster my-cluster --services {} \
    --query "services[?runningCount==\`0\`].{Name:serviceName,Desired:desiredCount,Status:status}" \
    --output table
```

> **Recommendations:**
> - Tasks consistently using < 50% of allocated CPU/memory → recommend downsizing to next valid Fargate increment
> - Services on standard FARGATE with non-critical workloads → recommend evaluating FARGATE_SPOT (up to 70% savings)
> - Container Insights must be enabled on the cluster to get per-service metrics

### ECS vs EKS — Cost Comparison by Workload

Use this decision matrix when asked to compare or evaluate migration between the two:

| Factor | ECS (Fargate) | EKS (Karpenter + Spot) |
|--------|--------------|------------------------|
| **Baseline cost** | ~$0.04048/vCPU-hr + $0.004445/GB-hr | EC2 Spot ~50–70% off On-Demand |
| **Operational overhead** | Very low — no nodes to manage | Higher — node pools, Karpenter config |
| **Minimum billing unit** | Per task (1 min minimum) | Per node (EC2 billing) |
| **Idle cost** | Zero when no tasks running | Node stays up until consolidated |
| **Best for** | Bursty, short-lived, low-ops teams | High-scale, steady workloads, complex scheduling |
| **Break-even point** | < ~4 vCPU sustained | > ~4 vCPU sustained 24/7 |

```python
# Rough monthly cost estimate — plug in actual values
vcpu_per_task = 0.5
mem_gb_per_task = 1.0
tasks = 10
hours = 730  # monthly

vcpu_cost = 0.04048 * vcpu_per_task * tasks * hours
mem_cost  = 0.004445 * mem_gb_per_task * tasks * hours
print(f"Fargate:        ${vcpu_cost + mem_cost:.2f}/month")

# EKS: 3x m7g.large (Graviton Spot ~$0.04/hr) + EKS cluster fee ($0.10/hr)
node_cost    = 3 * 0.04 * hours
cluster_cost = 0.10 * hours
print(f"EKS (3x Spot):  ${node_cost + cluster_cost:.2f}/month")
print(f"EKS break-even: consider EKS when sustained load exceeds ~6 vCPU")
```

**Rules of thumb:**
- **Recommend ECS Fargate** when: tasks are bursty/short-lived, team is small, sustained load < 4 vCPU
- **Recommend EKS + Karpenter** when: workload is steady/high-CPU, complex scheduling needed, or already Kubernetes-native
- **Fargate on EKS** is a middle ground: Kubernetes API without node management, at Fargate prices

### RDS / Database — Cost Findings

```bash
# Find idle RDS instances (avg < 1 connection over 7 days)
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=my-db \
  --start-time $(date -d '7 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 604800 \
  --statistics Average,Maximum

# List all RDS instances and their class (identify oversized classes)
aws rds describe-db-instances \
  --query "DBInstances[].{ID:DBInstanceIdentifier,Class:DBInstanceClass,Engine:Engine,Status:DBInstanceStatus,MultiAZ:MultiAZ,Storage:AllocatedStorage}" \
  --output table

# RDS storage autoscaling — check if max storage is set unnecessarily high
aws rds describe-db-instances \
  --query "DBInstances[].{ID:DBInstanceIdentifier,Storage:AllocatedStorage,MaxStorage:MaxAllocatedStorage}" \
  --output table

# Snapshots older than 30 days (manual snapshots accumulate cost)
aws rds describe-db-snapshots \
  --snapshot-type manual \
  --query "DBSnapshots[?SnapshotCreateTime<='$(date -d '30 days ago' --iso-8601=seconds)'].{ID:DBSnapshotIdentifier,Created:SnapshotCreateTime,Size:AllocatedStorage,DB:DBInstanceIdentifier}" \
  --output table
```

> **Recommendation**: RDS instances with < 1 avg connection over 7 days are idle — flag for review. Multi-AZ on dev/test environments is typically unnecessary.

### Networking — Cost Findings

```bash
# Unused load balancers (no healthy targets)
aws elbv2 describe-load-balancers \
  --query "LoadBalancers[].LoadBalancerArn" --output text | \
  tr '\t' '\n' | while read arn; do
    healthy=$(aws elbv2 describe-target-health \
      --target-group-arn "$(aws elbv2 describe-target-groups \
        --load-balancer-arn "$arn" \
        --query 'TargetGroups[0].TargetGroupArn' --output text)" \
      --query "TargetHealthDescriptions[?TargetHealth.State=='healthy'] | length(@)" \
      --output text 2>/dev/null || echo 0)
    echo "LB: $arn  Healthy targets: $healthy"
  done

# NAT Gateway data processing cost (can be significant)
aws cloudwatch get-metric-statistics \
  --namespace AWS/NATGateway \
  --metric-name BytesOutToDestination \
  --dimensions Name=NatGatewayId,Value=nat-xxxxxxxxx \
  --start-time $(date -d '7 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 604800 \
  --statistics Sum
```

### CloudWatch Logs — Cost Findings

CloudWatch Logs charges for ingestion ($0.50/GB) and storage ($0.03/GB/month). Log groups with no retention policy store logs indefinitely.

```bash
# List all log groups with no retention policy set (Never Expire)
aws logs describe-log-groups \
  --query "logGroups[?retentionInDays==null].{Name:logGroupName,StoredBytes:storedBytes,RetentionDays:retentionInDays}" \
  --output table

# Log groups by stored bytes — identify largest consumers
aws logs describe-log-groups \
  --query "logGroups[].{Name:logGroupName,StoredGB:storedBytes,Retention:retentionInDays}" \
  --output json | python3 -c "
import json, sys
groups = json.load(sys.stdin)
groups.sort(key=lambda x: x.get('StoredGB') or 0, reverse=True)
for g in groups[:20]:
    gb = (g.get('StoredGB') or 0) / 1e9
    ret = g.get('Retention') or 'Never'
    print(f\"{gb:8.2f} GB  retention={ret:10s}  {g['Name']}\")
"

# Log groups not written to in 30+ days (candidates for deletion after review)
aws logs describe-log-groups \
  --query "logGroups[].{Name:logGroupName,LastEvent:retentionInDays,Stored:storedBytes}" \
  --output text | while read name retention stored; do
    last=$(aws logs describe-log-streams \
      --log-group-name "$name" \
      --order-by LastEventTime \
      --descending \
      --max-items 1 \
      --query "logStreams[0].lastEventTimestamp" \
      --output text 2>/dev/null)
    echo "$name  last_event=$last"
  done

# Monthly ingestion cost estimate by log group
aws logs describe-log-groups \
  --query "logGroups[].logGroupName" --output text | \
  tr '\t' '\n' | while read group; do
    bytes=$(aws logs get-log-group-fields \
      --log-group-name "$group" \
      --time $(date -d '30 days ago' +%s)000 \
      --query "logGroupFields[?name=='@ingestionTime'] | [0]" \
      --output text 2>/dev/null || echo 0)
    echo "$group: $bytes"
  done
```

> **Recommendation**: Flag all log groups with no retention policy. Common safe defaults: Lambda/ECS logs → 30 days, application logs → 90 days, audit/compliance logs → 1–7 years per policy. Log groups with > 1 GB stored and no retention set are high-priority findings.

### DynamoDB — Cost Findings

DynamoDB bills on read/write capacity (provisioned or on-demand) and storage. Provisioned tables with low utilization waste money; on-demand tables with predictable traffic are often cheaper to switch to provisioned.

```bash
# List all tables and their billing mode
aws dynamodb list-tables --output text | tr '\t' '\n' | while read table; do
  aws dynamodb describe-table --table-name "$table" \
    --query "Table.{Name:TableName,BillingMode:BillingModeSummary.BillingMode,RCU:ProvisionedThroughput.ReadCapacityUnits,WCU:ProvisionedThroughput.WriteCapacityUnits,SizeBytes:TableSizeBytes,ItemCount:ItemCount}" \
    --output table
done

# Provisioned tables with low consumed capacity (< 20% of provisioned)
# Check consumed RCU vs provisioned RCU over last 7 days
aws cloudwatch get-metric-statistics \
  --namespace AWS/DynamoDB \
  --metric-name ConsumedReadCapacityUnits \
  --dimensions Name=TableName,Value=my-table \
  --start-time $(date -d '7 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 604800 \
  --statistics Sum

aws cloudwatch get-metric-statistics \
  --namespace AWS/DynamoDB \
  --metric-name ConsumedWriteCapacityUnits \
  --dimensions Name=TableName,Value=my-table \
  --start-time $(date -d '7 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 604800 \
  --statistics Sum

# Check for throttled requests (over-provisioned in wrong direction or burst issue)
aws cloudwatch get-metric-statistics \
  --namespace AWS/DynamoDB \
  --metric-name ThrottledRequests \
  --dimensions Name=TableName,Value=my-table \
  --start-time $(date -d '7 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 604800 \
  --statistics Sum

# Tables with auto-scaling configured — check if target utilization is set appropriately
aws application-autoscaling describe-scalable-targets \
  --service-namespace dynamodb \
  --query "ScalableTargets[].{Resource:ResourceId,Min:MinCapacity,Max:MaxCapacity}" \
  --output table

# DynamoDB Contributor Insights — identify hot keys driving excess cost (if enabled)
aws dynamodb describe-contributor-insights \
  --table-name my-table \
  --query "ContributorInsightsStatus"
```

> **Recommendations:**
> - Provisioned tables consuming < 20% of provisioned capacity → recommend switching to on-demand or reducing provisioned units
> - On-demand tables with consistent, predictable traffic → may be cheaper on provisioned capacity
> - Tables with no auto-scaling and low utilization → flag for review
> - Large tables (> 100 GB) should have TTL enabled if data has a natural expiry

### Data Transfer — Cost Findings

Data transfer is often the most surprising cost. Key categories: internet egress (~$0.09/GB), cross-AZ (~$0.01/GB each way), NAT Gateway processing ($0.045/GB).

```bash
# Data transfer costs via Cost Explorer — break down by transfer type
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '30 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["EC2 - Other"]}}' \
  --group-by Type=DIMENSION,Key=USAGE_TYPE

# NAT Gateway — bytes processed (each GB costs ~$0.045)
aws cloudwatch get-metric-statistics \
  --namespace AWS/NATGateway \
  --metric-name BytesOutToDestination \
  --dimensions Name=NatGatewayId,Value=nat-xxxxxxxxx \
  --start-time $(date -d '30 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 2592000 \
  --statistics Sum

# List NAT Gateways and which AZs they serve
aws ec2 describe-nat-gateways \
  --filter Name=state,Values=available \
  --query "NatGateways[].{ID:NatGatewayId,VPC:VpcId,Subnet:SubnetId,State:State}" \
  --output table

# VPC endpoints — check if any exist (reduce NAT Gateway traffic for AWS services)
aws ec2 describe-vpc-endpoints \
  --query "VpcEndpoints[].{ID:VpcEndpointId,Service:ServiceName,Type:VpcEndpointType,State:State}" \
  --output table

# Check S3 access patterns — traffic going via internet vs VPC endpoint
# If S3 VPC endpoint is absent, all S3 traffic from private subnets goes through NAT Gateway
aws ec2 describe-vpc-endpoints \
  --filters Name=service-name,Values="com.amazonaws.*.s3" \
  --query "VpcEndpoints[].{ID:VpcEndpointId,VPC:VpcId,Service:ServiceName}" \
  --output table

# CloudFront data transfer (outbound to internet — check if CDN is being used where appropriate)
aws cloudwatch get-metric-statistics \
  --namespace AWS/CloudFront \
  --metric-name BytesDownloaded \
  --dimensions Name=DistributionId,Value=EDFDVBD6EXAMPLE \
  --start-time $(date -d '30 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --period 2592000 \
  --statistics Sum
```

> **Recommendations:**
> - High NAT Gateway data processing + no S3/DynamoDB VPC endpoints → recommend adding Gateway endpoints (free)
> - Cross-AZ data transfer appearing in costs → check if services are making unnecessary cross-AZ calls
> - High internet egress without CloudFront → evaluate CloudFront as a cost-reduction layer for static/cacheable content

### ECR — Cost Findings

ECR charges $0.10/GB/month for private image storage. Untagged images and old image versions accumulate silently.

```bash
# List repositories and their total size
aws ecr describe-repositories \
  --query "repositories[].repositoryName" --output text | \
  tr '\t' '\n' | while read repo; do
    size=$(aws ecr describe-images \
      --repository-name "$repo" \
      --query "imageDetails[].imageSizeInBytes" \
      --output text | awk '{s+=$1} END {printf "%.2f GB", s/1e9}')
    count=$(aws ecr describe-images \
      --repository-name "$repo" \
      --query "length(imageDetails)" \
      --output text)
    echo "$repo  images=$count  size=$size"
  done

# Untagged images (no tag = likely a pushed intermediate layer or replaced image)
aws ecr describe-repositories \
  --query "repositories[].repositoryName" --output text | \
  tr '\t' '\n' | while read repo; do
    aws ecr describe-images \
      --repository-name "$repo" \
      --filter tagStatus=UNTAGGED \
      --query "imageDetails[].{Repo:'$repo',Digest:imageDigest,Size:imageSizeInBytes,Pushed:imagePushedAt}" \
      --output table 2>/dev/null
  done

# Images older than 90 days (may be safe to remove after review)
aws ecr describe-repositories \
  --query "repositories[].repositoryName" --output text | \
  tr '\t' '\n' | while read repo; do
    aws ecr describe-images \
      --repository-name "$repo" \
      --query "imageDetails[?imagePushedAt<='$(date -d '90 days ago' +%Y-%m-%dT%H:%M:%S)'].{Repo:'$repo',Tags:imageTags,Digest:imageDigest,Pushed:imagePushedAt,SizeGB:imageSizeInBytes}" \
      --output table 2>/dev/null
  done

# Check if lifecycle policies exist on each repository
aws ecr describe-repositories \
  --query "repositories[].repositoryName" --output text | \
  tr '\t' '\n' | while read repo; do
    policy=$(aws ecr get-lifecycle-policy --repository-name "$repo" 2>&1)
    if echo "$policy" | grep -q "LifecyclePolicyNotFoundException"; then
      echo "NO LIFECYCLE POLICY: $repo"
    fi
  done
```

> **Recommendation**: Repositories with no lifecycle policy are candidates for cleanup rules — e.g., keep last 10 tagged images, delete untagged images after 1 day. Flag repositories where untagged images account for > 20% of stored size.

### Trusted Advisor — Broad Cost Check

Trusted Advisor (requires Business or Enterprise support plan) provides pre-built cost checks across many services in a single call.

```bash
# List all cost optimization checks
aws support describe-trusted-advisor-checks \
  --language en \
  --query "checks[?category=='cost_optimizing'].{ID:id,Name:name,Description:description}" \
  --output table

# Get results for all cost checks
aws support describe-trusted-advisor-check-summaries \
  --check-ids $(aws support describe-trusted-advisor-checks \
    --language en \
    --query "checks[?category=='cost_optimizing'].id" \
    --output text) \
  --query "summaries[].{Name:checkId,Status:status,ResourcesFlagged:resourcesSummary.resourcesFlagged,EstimatedSavings:categorySpecificSummary.costOptimizing.estimatedMonthlySavings}" \
  --output table

# Drill into a specific check result (e.g. low-utilization EC2 instances)
aws support describe-trusted-advisor-check-result \
  --check-id Qch7DwouX1 \
  --language en \
  --query "result.flaggedResources[].{Region:region,Status:status,Metadata:metadata}" \
  --output table
```

**Key Trusted Advisor cost check IDs:**

| Check | ID |
|-------|----|
| Low utilization EC2 instances | `Qch7DwouX1` |
| Idle load balancers | `hjLMh88uM8` |
| Underutilized EBS volumes | `DAvU99Dc4C` |
| Unassociated Elastic IPs | `Z4AUBRNSmz` |
| Idle RDS instances | `Ti39halfu8` |
| Underutilized Redshift clusters | `G31sQ1E9U` |
| Reserved Instance optimization | `1iG5NDGVre` |
| Savings Plan optimization | `vV8QCQLRSZ` |

---

## GCP Cost Optimization

### Billing Queries (BigQuery)

```sql
-- Top 10 services by cost this month
SELECT
  service.description AS service,
  ROUND(SUM(cost), 2) AS total_cost,
  currency
FROM `project.dataset.gcp_billing_export_v1_*`
WHERE DATE(_PARTITIONTIME) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY service, currency
ORDER BY total_cost DESC
LIMIT 10;

-- Daily cost trend — spot anomalies
SELECT
  DATE(usage_start_time) AS date,
  service.description AS service,
  ROUND(SUM(cost), 2) AS daily_cost
FROM `project.dataset.gcp_billing_export_v1_*`
WHERE DATE(_PARTITIONTIME) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY date, service
ORDER BY date DESC, daily_cost DESC;

-- Resources missing 'team' label
SELECT
  resource.name,
  service.description,
  ROUND(SUM(cost), 2) AS cost
FROM `project.dataset.gcp_billing_export_v1_*`
WHERE DATE(_PARTITIONTIME) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
  AND NOT EXISTS (
    SELECT 1 FROM UNNEST(labels) AS l WHERE l.key = 'team'
  )
GROUP BY resource.name, service.description
ORDER BY cost DESC;

-- SKU-level breakdown — find unexpected line items
SELECT
  sku.description AS sku,
  service.description AS service,
  ROUND(SUM(cost), 2) AS cost
FROM `project.dataset.gcp_billing_export_v1_*`
WHERE DATE(_PARTITIONTIME) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY sku, service
ORDER BY cost DESC
LIMIT 20;
```

### Committed Use Discount Recommendations

```bash
# CUD recommendations from Recommender (read-only)
gcloud recommender recommendations list \
  --project=my-project \
  --location=global \
  --recommender=google.compute.commitment.UsageCommitmentRecommender \
  --format="table(name,stateInfo.state,primaryImpact.costProjection.cost.units,description)"

# Check existing CUD utilization
gcloud compute commitments list --project=my-project \
  --format="table(name,region,plan,status,endTimestamp)"
```

### Idle Resource Detection

```bash
# Idle VM instances (Recommender)
gcloud recommender recommendations list \
  --project=my-project \
  --location=us-central1-a \
  --recommender=google.compute.instance.IdleResourceRecommender \
  --format="table(name,description,stateInfo.state,primaryImpact.costProjection.cost.units)"

# Idle persistent disks (unattached)
gcloud recommender recommendations list \
  --project=my-project \
  --location=us-central1 \
  --recommender=google.compute.disk.IdleResourceRecommender \
  --format="table(name,description,primaryImpact.costProjection.cost.units)"

# List all unattached disks directly
gcloud compute disks list \
  --filter="NOT users:*" \
  --format="table(name,zone,sizeGb,type,creationTimestamp)"

# VM rightsizing recommendations
gcloud recommender recommendations list \
  --project=my-project \
  --location=us-central1-a \
  --recommender=google.compute.instance.MachineTypeRecommender \
  --format="table(name,description,primaryImpact.costProjection.cost.units)"

# Unused IP addresses
gcloud compute addresses list \
  --filter="status=RESERVED" \
  --format="table(name,region,address,status)"
```

### GKE — Cost Findings

```bash
# Node utilization
kubectl top nodes

# Pods without resource requests
kubectl get pods -A -o json | \
  python3 -c "
import json, sys
pods = json.load(sys.stdin)['items']
for p in pods:
    for c in p['spec']['containers']:
        r = c.get('resources', {}).get('requests', {})
        if not r.get('cpu') or not r.get('memory'):
            print(p['metadata']['namespace'], p['metadata']['name'], c['name'])
"

# Check if Spot node pools exist
gcloud container node-pools list --cluster=my-cluster --zone=us-central1-a \
  --format="table(name,config.spot,config.machineType,autoscaling.enabled,autoscaling.minNodeCount,autoscaling.maxNodeCount)"

# VPA recommendations (if VPA is installed in recommendation mode)
kubectl get vpa -A -o json | \
  python3 -c "
import json, sys
vpas = json.load(sys.stdin)['items']
for v in vpas:
    ns = v['metadata']['namespace']
    name = v['metadata']['name']
    recs = v.get('status', {}).get('recommendation', {}).get('containerRecommendations', [])
    for r in recs:
        print(ns, name, r['containerName'],
              'target CPU:', r.get('target', {}).get('cpu'),
              'target mem:', r.get('target', {}).get('memory'))
"
```

> **Recommendation**: VPA should be deployed in `Off` mode first to collect recommendations without applying them. Review recommendations before switching to `Auto`.

### Cloud Storage — Cost Findings

```bash
# Buckets with no lifecycle policy
for bucket in $(gsutil ls); do
  policy=$(gsutil lifecycle get "$bucket" 2>&1)
  if echo "$policy" | grep -q "has no lifecycle"; then
    echo "NO LIFECYCLE: $bucket"
  fi
done

# Storage class distribution per bucket
gsutil du -s gs://my-bucket

# Objects in Standard class older than 90 days (tiering candidates)
gsutil ls -l gs://my-bucket/** | \
  awk -v cutoff="$(date -d '90 days ago' +%Y-%m-%d)" '$2 < cutoff {print $0}'
```

> **Recommendation template**: Buckets with objects older than 30 days and no lifecycle policy should be evaluated for tiered storage (Standard → Nearline at 30d → Coldline at 90d → Archive at 365d).

---

## Azure Cost Optimization

### Cost Analysis

```bash
# Monthly spend by resource group
az consumption usage list \
  --start-date $(date -d '30 days ago' +%Y-%m-%d) \
  --end-date $(date +%Y-%m-%d) \
  --query "[].{RG:resourceGroup, Cost:pretaxCost, Currency:currency}" \
  --output table

# Advisor cost recommendations (all)
az advisor recommendation list \
  --category Cost \
  --query "[].{Impact:impact, Resource:resourceMetadata.resourceId, Problem:shortDescription.problem, Solution:shortDescription.solution, Savings:extendedProperties.annualSavingsAmount}" \
  --output table

# Filter Advisor to rightsizing and shutdown recommendations
az advisor recommendation list \
  --category Cost \
  --query "[?contains(shortDescription.solution, 'right-size') || contains(shortDescription.solution, 'shut down') || contains(shortDescription.solution, 'deallocate')]" \
  --output table
```

### Reserved Instances & Savings Plans

```bash
# RI purchase recommendations
az reservations catalog show \
  --reserved-resource-type VirtualMachines \
  --location eastus

# Check RI utilization (flag if < 80%)
az consumption reservations summaries list \
  --grain monthly \
  --reservation-order-id <order-id>

# List existing reservations
az reservations reservation list \
  --reservation-order-id <order-id> \
  --query "[].{Name:name,State:properties.provisioningState,Sku:sku.name,Expiry:properties.expiryDate}" \
  --output table
```

### VM Rightsizing — Findings

```bash
# Advisor rightsizing recommendations (read-only)
az advisor recommendation list \
  --category Cost \
  --query "[?contains(shortDescription.solution, 'right-size')].{VM:resourceMetadata.resourceId,Recommendation:shortDescription.solution,Impact:impact,Savings:extendedProperties.annualSavingsAmount}" \
  --output table

# VMs that are stopped/deallocated (still paying for managed disks)
az vm list \
  --query "[?powerState!='VM running'].{Name:name,RG:resourceGroup,Size:hardwareProfile.vmSize,State:powerState}" \
  --show-details \
  --output table

# VM CPU metrics — identify underutilized VMs
az monitor metrics list \
  --resource /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Compute/virtualMachines/<vm> \
  --metric "Percentage CPU" \
  --start-time $(date -d '14 days ago' --iso-8601=seconds) \
  --end-time $(date --iso-8601=seconds) \
  --interval PT1H \
  --aggregation Average Maximum \
  --output table
```

### AKS — Cost Findings

```bash
# Node utilization
kubectl top nodes

# Check if cluster autoscaler is enabled
az aks show \
  --resource-group my-rg \
  --name my-cluster \
  --query "agentPoolProfiles[].{Name:name,Mode:mode,VMSize:vmSize,AutoscaleEnabled:enableAutoScaling,MinCount:minCount,MaxCount:maxCount,CurrentCount:count}" \
  --output table

# Check for Spot node pools
az aks nodepool list \
  --resource-group my-rg \
  --cluster-name my-cluster \
  --query "[].{Name:name,Priority:scaleSetPriority,VMSize:vmSize,Count:count,MinCount:minCount,MaxCount:maxCount}" \
  --output table

# Pods without resource requests
kubectl get pods -A -o json | \
  python3 -c "
import json, sys
pods = json.load(sys.stdin)['items']
for p in pods:
    for c in p['spec']['containers']:
        r = c.get('resources', {}).get('requests', {})
        if not r.get('cpu') or not r.get('memory'):
            print(p['metadata']['namespace'], p['metadata']['name'], c['name'])
"
```

### Blob Storage — Cost Findings

```bash
# Storage accounts without lifecycle policies
az storage account list \
  --query "[].{Name:name,RG:resourceGroup,Kind:kind,Tier:sku.tier}" \
  --output table

# Check lifecycle policy on a specific account
az storage account management-policy show \
  --account-name mystorageaccount \
  --resource-group my-rg 2>/dev/null || echo "No lifecycle policy configured"

# List blobs in Hot tier older than 30 days (tiering candidates)
az storage blob list \
  --account-name mystorageaccount \
  --container-name my-container \
  --query "[?properties.lastModified<='$(date -d '30 days ago' --iso-8601=seconds)' && properties.blobTier=='Hot'].{Name:name,Size:properties.contentLength,Modified:properties.lastModified,Tier:properties.blobTier}" \
  --output table
```

> **Recommendation template**: Storage accounts without lifecycle policies and Hot-tier blobs older than 30 days should be reviewed for Cool/Archive tiering.

### Unattached Resources

```bash
# Unattached managed disks
az disk list \
  --query "[?diskState=='Unattached'].{Name:name,RG:resourceGroup,Size:diskSizeGb,SKU:sku.name,Created:timeCreated}" \
  --output table

# Unused public IP addresses
az network public-ip list \
  --query "[?ipConfiguration==null].{Name:name,RG:resourceGroup,IP:ipAddress,SKU:sku.name}" \
  --output table

# Unused load balancers (no backend pools)
az network lb list \
  --query "[].{Name:name,RG:resourceGroup,BackendPools:backendAddressPools}" \
  --output json | python3 -c "
import json, sys
lbs = json.load(sys.stdin)
for lb in lbs:
    if not lb.get('BackendPools'):
        print(f'NO BACKENDS: {lb[\"RG\"]}/{lb[\"Name\"]}')
"
```

---

## Multi-Cloud Tagging Audit

Tagging is the foundation of cost attribution. Surface untagged resources for remediation.

```bash
# Required tags to check for
# Environment: prod | staging | dev | sandbox
# Team:        <team name>
# CostCenter:  <finance code>
# Project:     <project slug>
# Owner:       <email or team alias>

# AWS — resources missing tags (via Resource Groups Tagging API)
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Environment \
  --query "ResourceTagMappingList[?Tags[?Key=='Environment']|length(@)==\`0\`].ResourceARN" \
  --output table

# AWS — EC2 instances missing Environment tag
aws ec2 describe-instances \
  --query "Reservations[].Instances[?!not_null(Tags[?Key=='Environment'].Value)].{ID:InstanceId,Type:InstanceType}" \
  --output table

# GCP — VMs missing 'team' label
gcloud compute instances list \
  --format="table(name,zone,machineType,labels)" \
  --filter="NOT labels.team:*"

# Azure — resources missing Environment tag
az resource list \
  --query "[?tags.Environment==null].{Name:name,Type:type,RG:resourceGroup}" \
  --output table
```

## Cost Anomaly Detection — Status Check

Check whether anomaly detection and budgets are configured (read-only):

```bash
# AWS — list existing anomaly monitors
aws ce list-cost-allocation-tags --status Active --output table
aws ce get-anomaly-monitors --output table

# AWS — list anomaly subscriptions
aws ce get-anomaly-subscriptions --output table

# AWS — recent anomalies detected
aws ce get-anomalies \
  --date-interval StartDate=$(date -d '30 days ago' +%Y-%m-%d),EndDate=$(date +%Y-%m-%d) \
  --output table

# GCP — list existing budgets
gcloud billing budgets list \
  --billing-account=BILLING_ACCOUNT_ID \
  --format="table(name,displayName,amount.specifiedAmount.units,thresholdRules)"

# Azure — list existing budgets
az consumption budget list --output table
```

> If no anomaly monitors or budgets exist, recommend setting them up with alerts at 50%, 90%, and 100% of expected monthly spend.

---

## Cost Review Checklist

Use this when performing a cost review. Items are findings to surface, not actions to take.

**Compute**
- [ ] Identify stopped EC2/VMs running > 7 days with no apparent reason
- [ ] Flag instances marked Overprovisioned by Compute Optimizer / Advisor
- [ ] Identify On-Demand instances running stateless workloads (Spot candidates)
- [ ] Check for ECS tasks consistently using < 50% of allocated CPU/memory
- [ ] Identify ECS services on standard FARGATE that could use FARGATE_SPOT
- [ ] Flag EKS/GKE/AKS pods without CPU/memory requests
- [ ] Check if Karpenter (EKS) or cluster autoscaler is enabled and consolidating nodes
- [ ] Flag non-Spot node pools running non-critical workloads

**Storage & Logs**
- [ ] Identify object storage buckets / ECR repositories with no lifecycle policy
- [ ] List unattached EBS volumes / GCP persistent disks / Azure managed disks
- [ ] Flag manual snapshots/backups older than 30 days with no retention policy
- [ ] Identify Hot-tier blobs/objects not accessed in 30+ days
- [ ] Flag CloudWatch log groups with no retention policy (logs stored indefinitely)
- [ ] List ECR untagged images and images older than 90 days

**Database**
- [ ] Flag DynamoDB provisioned tables with < 20% capacity utilization
- [ ] Identify idle RDS instances (< 1 avg connection over 7 days)
- [ ] Flag RDS Multi-AZ on dev/test environments
- [ ] List RDS manual snapshots older than 30 days

**Networking & Data Transfer**
- [ ] Identify load balancers with no healthy targets / empty backend pools
- [ ] List unattached public IPs / Elastic IPs / static addresses
- [ ] Check NAT Gateway data processing volume — flag if S3/DynamoDB VPC endpoints are absent
- [ ] Review data transfer line items in Cost Explorer for cross-AZ or internet egress surprises

**Commitments**
- [ ] Flag RI/CUD/Savings Plans utilization below 80%
- [ ] Check if Compute Optimizer / Recommender has pending commitment recommendations

**Governance**
- [ ] List resources missing required tags (Environment, Team, CostCenter)
- [ ] Verify cost anomaly monitors/budgets exist for all accounts/projects/subscriptions
- [ ] Check for budgets without alerts at 90% and 100% thresholds

## References

- [AWS Cost Optimization Pillar](https://docs.aws.amazon.com/wellarchitected/latest/cost-optimization-pillar/)
- [AWS Compute Optimizer](https://aws.amazon.com/compute-optimizer/)
- [AWS Cost Explorer API](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/)
- [GCP Cost Management](https://cloud.google.com/cost-management)
- [GCP Recommender](https://cloud.google.com/recommender/docs)
- [Azure Cost Management](https://learn.microsoft.com/en-us/azure/cost-management-billing/)
- [Azure Advisor Cost Recommendations](https://learn.microsoft.com/en-us/azure/advisor/advisor-cost-recommendations)
- [Lambda Power Tuning](https://github.com/alexcasalboni/aws-lambda-power-tuning)
- [Kubecost](https://www.kubecost.com/)
- [AWS Trusted Advisor](https://docs.aws.amazon.com/awssupport/latest/user/trusted-advisor.html)
- [AWS VPC Endpoints](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints.html)
