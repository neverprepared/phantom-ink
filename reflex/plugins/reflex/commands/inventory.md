---
description: Multi-cloud resource graph explorer — index, diagram, and diff AWS/Azure/GCP infrastructure
allowed-tools: Bash(*), AskUserQuestion(*), Write(*)
argument-hint: [explore|show|diagram|refresh|diff|summary] <resource> [--provider aws|azure|gcp] [--depth N] [--format auto|mermaid|dot] [--filter F]
---

# Cloud Inventory

Explore cloud infrastructure as a graph. Start from any resource, walk its relationships, and build a local index. Generate diagrams from the stored graph without hitting the cloud APIs again.

**SAFETY: All cloud queries are READ-ONLY. Never call CLI commands that create, modify, or delete resources.**

## Paths

```bash
INVENTORY_PY="${CLAUDE_PLUGIN_ROOT}/scripts/inventory.py"
DB_PATH="${REFLEX_HOME:-$HOME/.config/reflex}/inventory.db"
```

---

## Subcommands

### `/reflex:inventory` or `/reflex:inventory summary`

Show what's currently in the index.

**Instructions:**

1. Run:
   ```bash
   python3 "$INVENTORY_PY" summary
   ```
2. Display:
   ```
   Cloud Inventory Index
   ──────────────────────────────────────────────
   Total resources: 47   Last scan: 2026-03-31 14:22

   AWS (31)
     vpc              4
     subnet          12
     security-group   8
     ec2              5
     rds              2

   Azure (16)
     vnet             2
     subnet           6
     vm               4
     nsg              4
   ```
3. If empty: "Index is empty. Use `/reflex:inventory explore <resource>` to start building it."

---

### `/reflex:inventory explore <resource>`

Walk the resource graph from a starting resource. Fetches related resources from the cloud API and stores them in the index.

**Instructions:**

1. Parse arguments:
   - Positional: resource name, native ID, or filter expression (`tag:env=prod`, `type:vpc`, `name:*prod*`)
   - `--provider aws|azure|gcp` (optional — required for live lookup if not in index)
   - `--depth N` (default: 2)

2. Check prerequisites for the specified provider:

   ```bash
   # AWS
   aws sts get-caller-identity --output json

   # Azure
   az account show --output json

   # GCP
   gcloud config get-value project
   ```

   If the check fails, stop and tell the user to authenticate first (`aws configure`, `az login`, `gcloud auth login`).

3. Run:
   ```bash
   python3 "$INVENTORY_PY" explore "$RESOURCE" \
     ${PROVIDER:+--provider "$PROVIDER"} \
     --depth "$DEPTH"
   ```

4. Parse the JSON response:
   - `"ambiguous": true` → multiple resources matched. The script printed matching options as JSON lines. Present them with `AskUserQuestion`:
     ```
     Multiple resources match. Which one?
     [1] aws:123456:us-east-1:vpc:vpc-0abc123  prod-vpc
     [2] aws:123456:us-west-2:vpc:vpc-0def456  prod-vpc-dr
     ```
     Re-run with the selected `id` as the query.
   - `"error"` → show the error message and stop.
   - Success → show result:
     ```
     Explored prod-vpc
     ──────────────────────────────────────────────
     Resources discovered: 23
     Relationships mapped: 31
     Depth: 2

     Run /reflex:inventory diagram prod-vpc to visualize.
     ```

---

### `/reflex:inventory show <resource>`

Display cached details of a resource from the index.

**Instructions:**

1. Run:
   ```bash
   python3 "$INVENTORY_PY" show "$RESOURCE" ${PROVIDER:+--provider "$PROVIDER"}
   ```

2. If multiple matches, use `AskUserQuestion` to disambiguate.

3. Display the resource details in a readable format:
   ```
   vpc-0abc123def456789a  (aws:123456789012:us-east-1:vpc)
   ──────────────────────────────────────────────
   Name:          prod-vpc
   Status:        available
   Provider:      aws
   Account:       123456789012
   Region:        us-east-1
   Service:       vpc
   Discovered:    2026-03-31 14:20
   Last scanned:  2026-03-31 14:22
   Explored depth: 2

   Properties:
     CidrBlock: 10.0.0.0/16
     IsDefault: false
     DhcpOptionsId: dopt-0abc123

   Tags:
     Name: prod-vpc
     env:  prod
   ```

4. Show related resources (from `resource_relationships`):
   ```
   Related resources (23):
     contains       subnet-0abc → prod-subnet-1a
     contains       subnet-0def → prod-subnet-1b
     contains       sg-0abc     → prod-app-sg
     attached_to    igw-0abc    → prod-igw
   ```

---

### `/reflex:inventory diagram <resource>`

Generate a diagram of a resource's subgraph from the index.

**Instructions:**

1. Parse `--format auto|mermaid|dot` (default: auto).
   - auto: mermaid for ≤ 15 nodes, dot for > 15 nodes.

2. Run:
   ```bash
   python3 "$INVENTORY_PY" diagram "$RESOURCE" \
     ${PROVIDER:+--provider "$PROVIDER"} \
     --format "$FORMAT"
   ```

3. For **Mermaid** output:
   - Display the diagram inline in the response (renders in chat).
   - Also offer: "Save to file? [y/N]"
   - If yes, write to `${REFLEX_AZURE_DISCOVER_OUTPUT_DIR:-$HOME/Desktop}/<name>-diagram.md`

4. For **Graphviz DOT** output:
   - Write to `${REFLEX_AZURE_DISCOVER_OUTPUT_DIR:-$HOME/Desktop}/<name>-diagram.dot`
   - Attempt to render:
     ```bash
     dot -Tsvg "<name>-diagram.dot" -o "<name>-diagram.svg" 2>/dev/null
     ```
   - If `dot` is installed and succeeds: report both `.dot` and `.svg` paths.
   - If `dot` is not installed: report `.dot` path and tell user to install Graphviz (`brew install graphviz`).

---

### `/reflex:inventory refresh <resource>`

Re-walk a resource's neighborhood and upsert stale entries.

**Instructions:**

1. Run:
   ```bash
   python3 "$INVENTORY_PY" refresh "$RESOURCE" \
     ${PROVIDER:+--provider "$PROVIDER"} \
     --depth "$DEPTH"
   ```

2. Disambiguate if multiple matches.

3. Show result:
   ```
   Refreshed prod-vpc
   ──────────────────────────────────────────────
   Resources found: 25  (+2 since last scan)
   Relationships:   33  (+2)
   ```

---

### `/reflex:inventory diff <resource>`

Show what changed between the two most recent scans of a resource.

**Instructions:**

1. Run:
   ```bash
   python3 "$INVENTORY_PY" diff "$RESOURCE" ${PROVIDER:+--provider "$PROVIDER"}
   ```

2. Display:
   ```
   Diff — prod-vpc
   ──────────────────────────────────────────────
   Previous scan:  2026-03-30 09:11  (21 resources)
   Latest scan:    2026-03-31 14:22  (25 resources)
   Delta:          +4  (grew)
   ```

3. If only one scan exists: "Only one scan recorded — run `/reflex:inventory refresh` to create a second snapshot for diffing."

---

### `/reflex:inventory resolve <query>`

Find a resource in the index or live, without exploring. Useful for locating an ID before exploring.

**Instructions:**

1. Run:
   ```bash
   python3 "$INVENTORY_PY" resolve "$QUERY" \
     ${PROVIDER:+--provider "$PROVIDER"} \
     ${FILTER:+--filter "$FILTER"}
   ```

2. Display each match as a one-liner:
   ```
   aws:123456:us-east-1:vpc:vpc-0abc   prod-vpc        vpc    us-east-1
   aws:123456:us-west-2:vpc:vpc-0def   prod-vpc-dr     vpc    us-west-2
   ```

---

### No argument match

```
Usage: /reflex:inventory [subcommand] [resource] [options]

Subcommands:
  (default)                  Show index summary
  explore <resource>         Walk resource graph from a starting point
  show <resource>            Display cached resource details
  diagram <resource>         Generate Mermaid or Graphviz diagram
  refresh <resource>         Re-walk and upsert a resource's neighborhood
  diff <resource>            What changed since last scan
  resolve <query>            Find a resource without exploring

Options:
  --provider aws|azure|gcp   Scope to a specific cloud provider
  --depth N                  Traversal depth (default: 2)
  --format auto|mermaid|dot  Diagram format (default: auto)
  --filter tag:k=v|type:x    Filter for resolve command

Examples:
  /reflex:inventory explore prod-vpc --provider aws
  /reflex:inventory explore vpc-0abc123def --provider aws --depth 3
  /reflex:inventory explore --provider aws --filter tag:env=prod
  /reflex:inventory diagram prod-vpc --format mermaid
  /reflex:inventory refresh prod-vnet --provider azure
```
