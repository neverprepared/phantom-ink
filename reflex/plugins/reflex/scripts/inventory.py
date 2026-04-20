#!/usr/bin/env python3
"""
Reflex cloud inventory system — SQLite-backed multi-cloud resource graph.

Commands:
  resolve   <query> [--provider P] [--filter F]   Find resources by name, id, or filter
  explore   <query> [--provider P] [--depth N]    Walk resource graph from a starting resource
  show      <query> [--provider P]                Display cached resource details
  refresh   <query> [--provider P]                Re-walk a resource's neighborhood
  diff      <query> [--provider P]                What changed since last explore
  diagram   <query> [--provider P] [--format F]   Generate Graphviz DOT or Mermaid diagram
  summary                                         Index overview (counts by provider/service)

Filter syntax:
  tag:key=value     Match resources with this tag
  name:pattern      Glob-style name match (use * for wildcard)
  type:service      Match by service/resource type

Environment:
  REFLEX_HOME          Override for $HOME/.config/reflex
  AWS_PROFILE          AWS CLI profile
  AWS_DEFAULT_REGION   AWS region
  AZURE_SUBSCRIPTION_ID  Azure subscription (overrides CLI default)
  GCLOUD_PROJECT       GCP project (overrides gcloud config)
"""

import argparse
import fnmatch
import json
import os
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def db_path() -> Path:
    base = os.environ.get("REFLEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".config", "reflex"
    )
    return Path(base) / "inventory.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS resources (
    rowid           INTEGER PRIMARY KEY AUTOINCREMENT,
    id              TEXT NOT NULL UNIQUE,
    provider        TEXT NOT NULL,
    account         TEXT,
    region          TEXT,
    service         TEXT NOT NULL,
    resource_type   TEXT NOT NULL,
    resource_id     TEXT NOT NULL,
    name            TEXT,
    status          TEXT,
    properties_json TEXT,
    tags_json       TEXT,
    explored_depth  INTEGER DEFAULT 0,
    discovered_at   DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_scanned_at DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    explored_at     DATETIME
);

CREATE INDEX IF NOT EXISTS idx_res_provider  ON resources(provider);
CREATE INDEX IF NOT EXISTS idx_res_account   ON resources(account);
CREATE INDEX IF NOT EXISTS idx_res_region    ON resources(region);
CREATE INDEX IF NOT EXISTS idx_res_service   ON resources(service);
CREATE INDEX IF NOT EXISTS idx_res_name      ON resources(name);
CREATE INDEX IF NOT EXISTS idx_res_scanned   ON resources(last_scanned_at);

CREATE VIRTUAL TABLE IF NOT EXISTS resource_fts USING fts5(
    name, resource_id, service, resource_type,
    content='resources',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS res_ai AFTER INSERT ON resources BEGIN
    INSERT INTO resource_fts(rowid, name, resource_id, service, resource_type)
    VALUES (new.rowid, new.name, new.resource_id, new.service, new.resource_type);
END;

CREATE TRIGGER IF NOT EXISTS res_au AFTER UPDATE ON resources BEGIN
    INSERT INTO resource_fts(resource_fts, rowid, name, resource_id, service, resource_type)
    VALUES ('delete', old.rowid, old.name, old.resource_id, old.service, old.resource_type);
    INSERT INTO resource_fts(rowid, name, resource_id, service, resource_type)
    VALUES (new.rowid, new.name, new.resource_id, new.service, new.resource_type);
END;

CREATE TRIGGER IF NOT EXISTS res_ad AFTER DELETE ON resources BEGIN
    INSERT INTO resource_fts(resource_fts, rowid, name, resource_id, service, resource_type)
    VALUES ('delete', old.rowid, old.name, old.resource_id, old.service, old.resource_type);
END;

CREATE TABLE IF NOT EXISTS resource_relationships (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    rel_type     TEXT NOT NULL,
    UNIQUE(source_id, target_id, rel_type),
    FOREIGN KEY (source_id) REFERENCES resources(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES resources(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_rel_source ON resource_relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON resource_relationships(target_id);

CREATE TABLE IF NOT EXISTS scan_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id        TEXT,
    provider       TEXT NOT NULL,
    started_at     DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at   DATETIME,
    resource_count INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'running'
);
"""


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Relationship rules
# ---------------------------------------------------------------------------

@dataclass
class RelRule:
    """Defines how to fetch resources related to a given resource."""
    cmd: str            # CLI command; supports {id}, {name}, {account}, {region}, {rg}
    service: str        # service label for discovered resources
    resource_type: str  # resource_type label
    rel_type: str       # relationship type: contains, routes_to, attached_to, peered_with, etc.
    list_key: str = ""  # JSON key containing the list in response (empty = response is the list)
    id_field: str = ""  # field name for the native resource ID in each item
    name_field: str = "Name"  # field name for the human name


# AWS relationship rules keyed by service
AWS_RULES: dict[str, list[RelRule]] = {
    "vpc": [
        RelRule("aws ec2 describe-subnets --filters Name=vpc-id,Values={id} --output json",
                "subnet", "subnet", "contains", "Subnets", "SubnetId", "Tags[?Key=='Name']|[0].Value"),
        RelRule("aws ec2 describe-route-tables --filters Name=vpc-id,Values={id} --output json",
                "route-table", "route-table", "contains", "RouteTables", "RouteTableId", "Tags[?Key=='Name']|[0].Value"),
        RelRule("aws ec2 describe-security-groups --filters Name=vpc-id,Values={id} --output json",
                "security-group", "security-group", "contains", "SecurityGroups", "GroupId", "GroupName"),
        RelRule("aws ec2 describe-internet-gateways --filters Name=attachment.vpc-id,Values={id} --output json",
                "internet-gateway", "igw", "attached_to", "InternetGateways", "InternetGatewayId", "Tags[?Key=='Name']|[0].Value"),
        RelRule("aws ec2 describe-nat-gateways --filter Name=vpc-id,Values={id} --output json",
                "nat-gateway", "nat-gw", "contains", "NatGateways", "NatGatewayId", "Tags[?Key=='Name']|[0].Value"),
        RelRule("aws ec2 describe-vpc-peering-connections --filters Name=requester-vpc-info.vpc-id,Values={id} --output json",
                "vpc-peering", "vpc-peering", "peered_with", "VpcPeeringConnections", "VpcPeeringConnectionId", "Tags[?Key=='Name']|[0].Value"),
    ],
    "subnet": [
        RelRule("aws ec2 describe-instances --filters Name=subnet-id,Values={id} --output json",
                "ec2", "instance", "contains", "Reservations[].Instances[]", "InstanceId", "Tags[?Key=='Name']|[0].Value"),
        RelRule("aws rds describe-db-instances --output json",
                "rds", "db-instance", "contains", "DBInstances", "DBInstanceIdentifier", "DBInstanceIdentifier"),
    ],
    "ec2": [
        RelRule("aws ec2 describe-volumes --filters Name=attachment.instance-id,Values={id} --output json",
                "ebs", "volume", "attached_to", "Volumes", "VolumeId", "Tags[?Key=='Name']|[0].Value"),
        RelRule("aws ec2 describe-network-interfaces --filters Name=attachment.instance-id,Values={id} --output json",
                "network-interface", "eni", "attached_to", "NetworkInterfaces", "NetworkInterfaceId", "Description"),
    ],
    "alb": [
        RelRule("aws elbv2 describe-target-groups --load-balancer-arn {arn} --output json",
                "target-group", "target-group", "routes_to", "TargetGroups", "TargetGroupArn", "TargetGroupName"),
        RelRule("aws elbv2 describe-listeners --load-balancer-arn {arn} --output json",
                "alb-listener", "listener", "contains", "Listeners", "ListenerArn", "Protocol"),
    ],
    "lambda": [
        RelRule("aws lambda get-function-configuration --function-name {id} --output json",
                "lambda-config", "function-config", "describes", "", "FunctionArn", "FunctionName"),
    ],
}

# Azure relationship rules keyed by service
AZURE_RULES: dict[str, list[RelRule]] = {
    "resource-group": [
        RelRule("az resource list --resource-group {name} --output json",
                "resource", "resource", "contains", "", "id", "name"),
    ],
    "vnet": [
        RelRule("az network vnet subnet list --vnet-name {name} --resource-group {rg} --output json",
                "subnet", "subnet", "contains", "", "id", "name"),
        RelRule("az network vnet peering list --vnet-name {name} --resource-group {rg} --output json",
                "vnet-peering", "peering", "peered_with", "", "id", "name"),
    ],
    "subnet": [
        RelRule("az vm list --query \"[?networkProfile.networkInterfaces[0].id!=null]\" --output json",
                "vm", "virtual-machine", "contains", "", "id", "name"),
    ],
    "vm": [
        RelRule("az vm show --name {name} --resource-group {rg} --output json",
                "vm-detail", "detail", "describes", "", "id", "name"),
        RelRule("az disk list --query \"[?managedBy!=null]\" --resource-group {rg} --output json",
                "disk", "managed-disk", "attached_to", "", "id", "name"),
    ],
    "aks": [
        RelRule("az aks show --name {name} --resource-group {rg} --output json",
                "aks-detail", "detail", "describes", "", "id", "name"),
    ],
    "nsg": [
        RelRule("az network nsg show --name {name} --resource-group {rg} --output json",
                "nsg-detail", "detail", "describes", "", "id", "name"),
    ],
}

# GCP relationship rules keyed by service
GCP_RULES: dict[str, list[RelRule]] = {
    "vpc-network": [
        RelRule("gcloud compute networks subnets list --filter=\"network~{name}\" --format=json",
                "subnet", "subnet", "contains", "", "selfLink", "name"),
        RelRule("gcloud compute firewall-rules list --filter=\"network~{name}\" --format=json",
                "firewall-rule", "firewall-rule", "secures", "", "selfLink", "name"),
        RelRule("gcloud compute routers list --filter=\"network~{name}\" --format=json",
                "router", "cloud-router", "routes_via", "", "selfLink", "name"),
    ],
    "subnet": [
        RelRule("gcloud compute instances list --filter=\"networkInterfaces.subnetwork~{name}\" --format=json",
                "vm", "instance", "contains", "", "selfLink", "name"),
    ],
    "vm": [
        RelRule("gcloud compute instances describe {name} --zone={region} --format=json",
                "vm-detail", "detail", "describes", "", "selfLink", "name"),
    ],
    "gke-cluster": [
        RelRule("gcloud container clusters describe {name} --zone={region} --format=json",
                "gke-detail", "detail", "describes", "", "selfLink", "name"),
    ],
}

RULES = {"aws": AWS_RULES, "azure": AZURE_RULES, "gcp": GCP_RULES}


# ---------------------------------------------------------------------------
# Provider context (account / subscription / project)
# ---------------------------------------------------------------------------

_ctx_cache: dict[str, str] = {}


def get_account(provider: str) -> str:
    if provider in _ctx_cache:
        return _ctx_cache[provider]
    try:
        if provider == "aws":
            out = _run(["aws", "sts", "get-caller-identity", "--output", "json"])
            val = json.loads(out).get("Account", "unknown")
        elif provider == "azure":
            env_val = os.environ.get("AZURE_SUBSCRIPTION_ID")
            if env_val:
                val = env_val
            else:
                out = _run(["az", "account", "show", "--output", "json"])
                val = json.loads(out).get("id", "unknown")
        elif provider == "gcp":
            env_val = os.environ.get("GCLOUD_PROJECT") or os.environ.get("CLOUDSDK_CORE_PROJECT")
            if env_val:
                val = env_val
            else:
                val = _run(["gcloud", "config", "get-value", "project"]).strip()
        else:
            val = "unknown"
    except Exception:
        val = "unknown"
    _ctx_cache[provider] = val
    return val


def _run(cmd: list[str], check: bool = False) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Resource normalization
# ---------------------------------------------------------------------------

def make_id(provider: str, account: str, region: str, service: str, resource_id: str) -> str:
    region = (region or "global").lower()
    return f"{provider}:{account}:{region}:{service}:{resource_id}"


def extract_name_aws(item: dict, name_field: str) -> str | None:
    """Extract Name tag from AWS resource."""
    tags = item.get("Tags") or []
    for t in tags:
        if t.get("Key") == "Name":
            return t.get("Value")
    return item.get(name_field) or item.get("Name")


def extract_region_aws(item: dict) -> str:
    az = item.get("AvailabilityZone", "")
    if az and len(az) > 1:
        return az[:-1]  # strip trailing letter
    return item.get("Region", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


def normalize_aws(item: dict, service: str, resource_type: str, id_field: str, name_field: str, account: str) -> dict:
    resource_id = _nested_get(item, id_field) or ""
    name = extract_name_aws(item, name_field)
    region = extract_region_aws(item)
    return {
        "id":              make_id("aws", account, region, service, resource_id),
        "provider":        "aws",
        "account":         account,
        "region":          region,
        "service":         service,
        "resource_type":   resource_type,
        "resource_id":     resource_id,
        "name":            name,
        "status":          item.get("State", item.get("Status", "")),
        "properties_json": json.dumps(item),
        "tags_json":       json.dumps(item.get("Tags") or []),
    }


def normalize_azure(item: dict, service: str, resource_type: str, account: str) -> dict:
    resource_id = item.get("id") or item.get("name") or ""
    name = item.get("name") or ""
    location = item.get("location") or "global"
    return {
        "id":              make_id("azure", account, location, service, name or resource_id),
        "provider":        "azure",
        "account":         account,
        "region":          location,
        "service":         service,
        "resource_type":   resource_type,
        "resource_id":     resource_id,
        "name":            name,
        "status":          item.get("provisioningState", ""),
        "properties_json": json.dumps(item),
        "tags_json":       json.dumps(item.get("tags") or {}),
    }


def normalize_gcp(item: dict, service: str, resource_type: str, account: str) -> dict:
    resource_id = item.get("selfLink") or item.get("name") or ""
    name = item.get("name") or ""
    region = _gcp_region(item)
    return {
        "id":              make_id("gcp", account, region, service, name),
        "provider":        "gcp",
        "account":         account,
        "region":          region,
        "service":         service,
        "resource_type":   resource_type,
        "resource_id":     resource_id,
        "name":            name,
        "status":          item.get("status", ""),
        "properties_json": json.dumps(item),
        "tags_json":       json.dumps(item.get("labels") or {}),
    }


def _gcp_region(item: dict) -> str:
    zone = item.get("zone", "")
    region = item.get("region", "")
    if region:
        return region.split("/")[-1]
    if zone:
        z = zone.split("/")[-1]
        return "-".join(z.split("-")[:-1])
    return "global"


def _nested_get(obj: dict, path: str) -> Any:
    """Simple dot-notation getter; returns None if path not found."""
    parts = path.split(".")
    cur = obj
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO resources
    (id, provider, account, region, service, resource_type, resource_id, name,
     status, properties_json, tags_json, last_scanned_at)
VALUES
    (:id, :provider, :account, :region, :service, :resource_type, :resource_id, :name,
     :status, :properties_json, :tags_json, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
ON CONFLICT(id) DO UPDATE SET
    name            = excluded.name,
    status          = excluded.status,
    properties_json = excluded.properties_json,
    tags_json       = excluded.tags_json,
    last_scanned_at = excluded.last_scanned_at
"""


def upsert_resource(conn: sqlite3.Connection, r: dict):
    r.setdefault("status", "")
    r.setdefault("tags_json", "[]")
    conn.execute(UPSERT_SQL, r)


def upsert_relationship(conn: sqlite3.Connection, source_id: str, target_id: str, rel_type: str):
    conn.execute(
        "INSERT OR IGNORE INTO resource_relationships (source_id, target_id, rel_type) VALUES (?,?,?)",
        (source_id, target_id, rel_type),
    )


def mark_explored(conn: sqlite3.Connection, resource_id: str, depth: int):
    conn.execute(
        "UPDATE resources SET explored_depth=?, explored_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
        (depth, resource_id),
    )


# ---------------------------------------------------------------------------
# Resolution: find resource by name, native ID, or filter
# ---------------------------------------------------------------------------

def resolve(conn: sqlite3.Connection, query: str, provider: str | None = None,
            filter_expr: str | None = None) -> list[dict]:
    """
    Find resources by:
      - native resource ID (exact)
      - name (FTS + glob)
      - tag:key=value filter
      - type:service filter
    Returns list of resource dicts (may be empty or multiple → caller disambiguates).
    """
    results = []

    # --- index lookup first ---
    where_clauses = []
    params: list[Any] = []

    if provider:
        where_clauses.append("provider = ?")
        params.append(provider)

    # Parse filter_expr
    if filter_expr:
        if filter_expr.startswith("tag:"):
            kv = filter_expr[4:]
            if "=" in kv:
                k, v = kv.split("=", 1)
                where_clauses.append("tags_json LIKE ?")
                params.append(f'%"{k}"%"{v}"%')
        elif filter_expr.startswith("type:"):
            where_clauses.append("service = ?")
            params.append(filter_expr[5:])
        elif filter_expr.startswith("name:"):
            # handled below via glob
            pass

    # Exact resource_id match
    exact_where = " AND ".join(where_clauses + ["resource_id = ?"]) if where_clauses else "resource_id = ?"
    rows = conn.execute(f"SELECT * FROM resources WHERE {exact_where}", params + [query]).fetchall()
    results.extend([dict(r) for r in rows])

    # FTS name search
    if not results:
        try:
            fts_rows = conn.execute(
                f"""
                SELECT r.* FROM resources r
                JOIN resource_fts f ON f.rowid = r.rowid
                WHERE resource_fts MATCH ?
                {"AND provider = ?" if provider else ""}
                ORDER BY rank LIMIT 20
                """,
                [query] + ([provider] if provider else []),
            ).fetchall()
            results.extend([dict(r) for r in fts_rows])
        except Exception:
            pass

    # Glob name match (for name:* patterns)
    if not results:
        pattern = query if "*" in query else f"*{query}*"
        where = " AND ".join(where_clauses + ["name LIKE ?"]) if where_clauses else "name LIKE ?"
        like = pattern.replace("*", "%")
        rows = conn.execute(
            f"SELECT * FROM resources WHERE {where} LIMIT 20",
            params + [like],
        ).fetchall()
        results.extend([dict(r) for r in rows])

    return _dedupe(results)


def _dedupe(rows: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for r in rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out


def resolve_live(query: str, provider: str) -> list[dict]:
    """Query cloud CLI to find a resource not yet in the index."""
    account = get_account(provider)
    results = []
    try:
        if provider == "aws":
            out = _run(["aws", "resourcegroupstaggingapi", "get-resources",
                        "--tag-filters", f"Key=Name,Values={query}", "--output", "json"])
            data = json.loads(out)
            for r in data.get("ResourceTagMappingList", []):
                arn = r.get("ResourceARN", "")
                parts = arn.split(":")
                service = parts[2] if len(parts) > 2 else "unknown"
                region  = parts[3] if len(parts) > 3 else "unknown"
                resource_id = parts[-1].split("/")[-1]
                results.append({
                    "id":            make_id("aws", account, region, service, resource_id),
                    "provider":      "aws",
                    "account":       account,
                    "region":        region,
                    "service":       service,
                    "resource_type": service,
                    "resource_id":   resource_id,
                    "name":          query,
                    "status":        "",
                    "properties_json": json.dumps(r),
                    "tags_json":     json.dumps(r.get("Tags", [])),
                })

        elif provider == "azure":
            out = _run(["az", "resource", "list", "--name", query, "--output", "json"])
            items = json.loads(out)
            for item in items:
                results.append(normalize_azure(item, item.get("type", "resource"), "resource", account))

        elif provider == "gcp":
            out = _run(["gcloud", "asset", "search-all-resources",
                        f"--query=name:{query}", "--format=json"])
            items = json.loads(out)
            for item in items:
                asset_type = item.get("assetType", "").split("/")[-1].lower()
                results.append(normalize_gcp(item, asset_type, asset_type, account))

    except Exception:
        pass

    return results


# ---------------------------------------------------------------------------
# Traversal engine
# ---------------------------------------------------------------------------

def explore_resource(conn: sqlite3.Connection, resource: dict, max_depth: int, current_depth: int = 0):
    """Recursively walk the resource graph, fetching and storing related resources."""
    if current_depth >= max_depth:
        return

    provider = resource["provider"]
    service  = resource["service"]
    rules    = RULES.get(provider, {}).get(service, [])

    if not rules:
        return

    account = resource.get("account") or get_account(provider)
    region  = resource.get("region") or ""
    name    = resource.get("name") or resource["resource_id"]
    res_id  = resource["resource_id"]

    # Extract resource-group for Azure resources
    rg = ""
    if provider == "azure":
        try:
            props = json.loads(resource.get("properties_json") or "{}")
            rid = props.get("id") or ""
            parts = rid.split("/")
            rg_idx = next((i for i, p in enumerate(parts) if p.lower() == "resourcegroups"), -1)
            if rg_idx >= 0 and rg_idx + 1 < len(parts):
                rg = parts[rg_idx + 1]
        except Exception:
            pass

    for rule in rules:
        cmd = rule.cmd.format(
            id=res_id,
            name=name,
            account=account,
            region=region,
            rg=rg,
            arn=res_id,
        )

        try:
            out = _run(shlex.split(cmd))
            if not out.strip():
                continue
            data = json.loads(out)

            # Unwrap list key if specified
            if rule.list_key:
                for key in rule.list_key.split("."):
                    if key == "[]":
                        flat = []
                        for item in (data if isinstance(data, list) else [data]):
                            if isinstance(item, list):
                                flat.extend(item)
                            else:
                                flat.append(item)
                        data = flat
                    elif isinstance(data, dict):
                        data = data.get(key, [])

            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                continue

            for item in data:
                if provider == "aws":
                    r = normalize_aws(item, rule.service, rule.resource_type,
                                      rule.id_field, rule.name_field, account)
                elif provider == "azure":
                    r = normalize_azure(item, rule.service, rule.resource_type, account)
                elif provider == "gcp":
                    r = normalize_gcp(item, rule.service, rule.resource_type, account)
                else:
                    continue

                if not r["resource_id"]:
                    continue

                upsert_resource(conn, r)
                upsert_relationship(conn, resource["id"], r["id"], rule.rel_type)
                conn.commit()

                # Recurse
                explore_resource(conn, r, max_depth, current_depth + 1)

        except Exception:
            continue

    mark_explored(conn, resource["id"], max_depth - current_depth)
    conn.commit()


# ---------------------------------------------------------------------------
# Diagram generation
# ---------------------------------------------------------------------------

SERVICE_COLORS = {
    "vpc": "#AED6F1", "subnet": "#D5E8D4", "security-group": "#FFE6CC",
    "ec2": "#DAE8FC", "rds": "#E1D5E7", "lambda": "#FFF2CC",
    "alb": "#F8CECC", "s3": "#D5E8D4", "internet-gateway": "#F5F5F5",
    "nat-gateway": "#FFE6CC", "vpc-peering": "#E6D0DE",
    "vnet": "#AED6F1", "vm": "#DAE8FC", "nsg": "#FFE6CC",
    "aks": "#D5E8D4", "resource-group": "#F5F5F5",
    "vpc-network": "#AED6F1", "firewall-rule": "#FFE6CC",
    "gke-cluster": "#D5E8D4",
}


def subgraph_for(conn: sqlite3.Connection, root_id: str) -> tuple[list[dict], list[dict]]:
    """Return (nodes, edges) reachable from root_id."""
    visited = set()
    queue = [root_id]
    nodes = []
    edges = []

    while queue:
        cur = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)

        row = conn.execute("SELECT * FROM resources WHERE id = ?", (cur,)).fetchone()
        if row:
            nodes.append(dict(row))

        rels = conn.execute(
            "SELECT * FROM resource_relationships WHERE source_id = ?", (cur,)
        ).fetchall()
        for rel in rels:
            edges.append(dict(rel))
            if rel["target_id"] not in visited:
                queue.append(rel["target_id"])

    return nodes, edges


def generate_dot(conn: sqlite3.Connection, root_id: str) -> str:
    nodes, edges = subgraph_for(conn, root_id)
    if not nodes:
        return ""

    id_map = {n["id"]: f"n{i}" for i, n in enumerate(nodes)}
    lines = ['digraph inventory {', '  rankdir=LR;', '  node [shape=box, style=filled, fontsize=11];']

    for n in nodes:
        color = SERVICE_COLORS.get(n["service"], "#FFFFFF")
        label = (n["name"] or n["resource_id"])[:40]
        sublabel = n["service"]
        node_id = id_map[n["id"]]
        border = ', penwidth=2' if n["id"] == root_id else ''
        lines.append(f'  {node_id} [label="{label}\\n{sublabel}", fillcolor="{color}"{border}];')

    for e in edges:
        src = id_map.get(e["source_id"])
        tgt = id_map.get(e["target_id"])
        if src and tgt:
            lines.append(f'  {src} -> {tgt} [label="{e["rel_type"]}"];')

    lines.append("}")
    return "\n".join(lines)


def generate_mermaid(conn: sqlite3.Connection, root_id: str) -> str:
    nodes, edges = subgraph_for(conn, root_id)
    if not nodes:
        return ""

    id_map = {n["id"]: f"N{i}" for i, n in enumerate(nodes)}
    lines = ["```mermaid", "graph LR"]

    for n in nodes:
        label = (n["name"] or n["resource_id"])[:40]
        sublabel = n["service"]
        nid = id_map[n["id"]]
        if n["id"] == root_id:
            lines.append(f'  {nid}["{label}<br/>{sublabel}"]:::root')
        else:
            lines.append(f'  {nid}["{label}<br/>{sublabel}"]')

    for e in edges:
        src = id_map.get(e["source_id"])
        tgt = id_map.get(e["target_id"])
        if src and tgt:
            lines.append(f"  {src} -->|{e['rel_type']}| {tgt}")

    lines.append("  classDef root fill:#AED6F1,stroke:#2980B9,stroke-width:2px")
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _pick_one(matches: list[dict]) -> dict | None:
    """Print ambiguous matches to stdout as JSON lines for the command layer to present."""
    for m in matches:
        print(json.dumps({
            "id": m["id"], "name": m["name"], "service": m["service"],
            "provider": m["provider"], "region": m["region"],
        }))
    return None


def cmd_resolve(args):
    conn = connect()
    query    = " ".join(args.query)
    provider = getattr(args, "provider", None)
    filter_e = getattr(args, "filter", None)

    matches = resolve(conn, query, provider, filter_e)

    if not matches and provider:
        matches = resolve_live(query, provider)
        for r in matches:
            upsert_resource(conn, r)
        conn.commit()

    conn.close()
    for m in matches:
        print(json.dumps({k: m[k] for k in ("id","provider","account","region","service","resource_type","resource_id","name","status","last_scanned_at")}))


def cmd_explore(args):
    conn = connect()
    query    = " ".join(args.query)
    provider = getattr(args, "provider", None)
    depth    = getattr(args, "depth", 2)

    matches = resolve(conn, query, provider)
    if not matches and provider:
        matches = resolve_live(query, provider)
        for r in matches:
            upsert_resource(conn, r)
        conn.commit()

    if not matches:
        print(json.dumps({"error": f"no resource found matching '{query}'"}), file=sys.stderr)
        conn.close()
        return

    if len(matches) > 1:
        print(json.dumps({"ambiguous": True, "matches": len(matches)}))
        _pick_one(matches)
        conn.close()
        return

    root = matches[0]

    scan_id = conn.execute(
        "INSERT INTO scan_history (root_id, provider) VALUES (?,?)",
        (root["id"], root["provider"]),
    ).lastrowid
    conn.commit()

    explore_resource(conn, root, depth)

    _, edges = subgraph_for(conn, root["id"])
    nodes_after, _ = subgraph_for(conn, root["id"])

    conn.execute(
        "UPDATE scan_history SET completed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'), resource_count=?, status='completed' WHERE id=?",
        (len(nodes_after), scan_id),
    )
    conn.commit()
    conn.close()

    print(json.dumps({
        "root":           root["id"],
        "name":           root["name"],
        "resources_found": len(nodes_after),
        "relationships":  len(edges),
        "depth":          depth,
    }))


def cmd_show(args):
    conn = connect()
    query    = " ".join(args.query)
    provider = getattr(args, "provider", None)

    matches = resolve(conn, query, provider)
    conn.close()

    if not matches:
        print(json.dumps({"error": f"not in index: '{query}'"}), file=sys.stderr)
        return

    for m in matches:
        print(json.dumps(dict(m)))


def cmd_diagram(args):
    conn = connect()
    query    = " ".join(args.query)
    provider = getattr(args, "provider", None)
    fmt      = getattr(args, "format", "auto")

    matches = resolve(conn, query, provider)
    if not matches:
        print(json.dumps({"error": f"not in index: '{query}'"}), file=sys.stderr)
        conn.close()
        return

    root = matches[0]
    nodes, _ = subgraph_for(conn, root["id"])
    node_count = len(nodes)

    if fmt == "auto":
        fmt = "mermaid" if node_count <= 15 else "dot"

    if fmt in ("dot", "graphviz"):
        print(generate_dot(conn, root["id"]))
    else:
        print(generate_mermaid(conn, root["id"]))

    conn.close()


def cmd_refresh(args):
    conn = connect()
    query    = " ".join(args.query)
    provider = getattr(args, "provider", None)
    depth    = getattr(args, "depth", 2)

    matches = resolve(conn, query, provider)
    if not matches:
        print(json.dumps({"error": f"not in index: '{query}'"}), file=sys.stderr)
        conn.close()
        return

    root = matches[0]
    # Clear existing relationships from this root before re-walking
    conn.execute("DELETE FROM resource_relationships WHERE source_id = ?", (root["id"],))
    conn.commit()

    explore_resource(conn, root, depth)
    nodes, edges = subgraph_for(conn, root["id"])
    conn.close()

    print(json.dumps({
        "root":           root["id"],
        "name":           root["name"],
        "resources_found": len(nodes),
        "relationships":  len(edges),
    }))


def cmd_diff(args):
    conn = connect()
    query    = " ".join(args.query)
    provider = getattr(args, "provider", None)

    matches = resolve(conn, query, provider)
    if not matches:
        print(json.dumps({"error": f"not in index: '{query}'"}), file=sys.stderr)
        conn.close()
        return

    root = matches[0]
    history = conn.execute(
        "SELECT * FROM scan_history WHERE root_id = ? ORDER BY started_at DESC LIMIT 2",
        (root["id"],),
    ).fetchall()
    conn.close()

    if len(history) < 2:
        print(json.dumps({"message": "only one scan recorded — nothing to diff yet"}))
        return

    latest, previous = dict(history[0]), dict(history[1])
    delta = latest["resource_count"] - previous["resource_count"]
    print(json.dumps({
        "root":              root["id"],
        "previous_scan":     previous["started_at"],
        "latest_scan":       latest["started_at"],
        "previous_count":    previous["resource_count"],
        "latest_count":      latest["resource_count"],
        "delta":             delta,
        "direction":         "grew" if delta > 0 else "shrank" if delta < 0 else "unchanged",
    }))


def cmd_summary(args):
    conn = connect()
    rows = conn.execute(
        """
        SELECT provider, service, COUNT(*) as count
        FROM resources
        GROUP BY provider, service
        ORDER BY provider, count DESC
        """
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    last_scan = conn.execute(
        "SELECT completed_at FROM scan_history WHERE status='completed' ORDER BY completed_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    print(json.dumps({
        "total_resources": total,
        "last_scan":       dict(last_scan)["completed_at"] if last_scan else None,
        "breakdown":       [dict(r) for r in rows],
    }))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Reflex cloud inventory")
    sub = parser.add_subparsers(dest="command")

    def add_query_args(p, depth=False):
        p.add_argument("query", nargs="+")
        p.add_argument("--provider", choices=["aws", "azure", "gcp"], default=None)
        if depth:
            p.add_argument("--depth", type=int, default=2)

    p = sub.add_parser("resolve");  add_query_args(p)
    p.add_argument("--filter", default=None)

    p = sub.add_parser("explore"); add_query_args(p, depth=True)
    p = sub.add_parser("show");    add_query_args(p)
    p = sub.add_parser("refresh"); add_query_args(p, depth=True)
    p = sub.add_parser("diff");    add_query_args(p)
    p = sub.add_parser("diagram"); add_query_args(p)
    p.add_argument("--format", choices=["auto", "dot", "graphviz", "mermaid"], default="auto")

    sub.add_parser("summary")

    args = parser.parse_args()

    dispatch = {
        "resolve": cmd_resolve,
        "explore": cmd_explore,
        "show":    cmd_show,
        "refresh": cmd_refresh,
        "diff":    cmd_diff,
        "diagram": cmd_diagram,
        "summary": cmd_summary,
    }

    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
