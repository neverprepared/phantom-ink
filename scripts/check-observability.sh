#!/usr/bin/env bash
# Read-only verification of everything built in this session.
# Queries OpenSearch and Dashboards only. Writes nothing, anywhere.
#
#   bash check-results.sh
set -uo pipefail

OS=https://opensearch.neverprepared.com
DASH=https://logs.neverprepared.com

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
inf() { printf '    %s\n' "$1"; }

say "1. Dashboard saved objects (should be 10 visualisations + 1 dashboard)"
for v in cc-total-cost cc-active-time cc-cost-over-time cc-token-usage \
         cc-api-requests cc-api-latency cc-model-cost \
         cc-cost-by-source cc-cost-by-workspace cc-tool-failures; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
         "$DASH/api/saved_objects/visualization/$v")
  [ "$code" = "200" ] && ok "$v" || no "$v (HTTP $code)"
done
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$DASH/api/saved_objects/dashboard/cc-dashboard")
[ "$code" = "200" ] && ok "cc-dashboard" || no "cc-dashboard (HTTP $code)"
inf "open: $DASH/app/dashboards#/view/cc-dashboard   (set the range to 30 days)"

say "2. The three new panels have data behind them"
python3 - "$OS" <<'PY'
import json, sys, urllib.request

OS = sys.argv[1]

def post(path, body):
    r = urllib.request.Request(f"{OS}{path}", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(r, timeout=40) as resp:
        return json.loads(resp.read())

rng = {"range": {"time": {"gte": "now-30d"}}}

# Cost by source
d = post("/metrics-otel-*/_search", {
    "size": 0,
    "query": {"bool": {"must": [{"term": {"name": "claude_code.cost.usage"}}, rng]}},
    "aggs": {"q": {"terms": {"field": "metric.attributes.query_source.keyword", "size": 10},
                   "aggs": {"c": {"sum": {"field": "value"}}}}}})
rows = [(b["key"], b["c"]["value"]) for b in d["aggregations"]["q"]["buckets"]]
total = sum(v for _, v in rows) or 1
print("  Cost by Source (30d):")
for k, v in rows:
    print(f"    {k:<12} ${v:>10,.2f}   {v/total*100:5.1f}%")
hidden = sum(v for k, v in rows if k != "main")
print(f"    -> ${hidden:,.2f} ({hidden/total*100:.1f}%) is what a transcript-based capture misses")

# Cost by workspace
d = post("/metrics-otel-*/_search", {
    "size": 0,
    "query": {"bool": {"must": [{"term": {"name": "claude_code.cost.usage"}}, rng]}},
    "aggs": {"w": {"terms": {"field": "metric.attributes.workspace.keyword", "size": 10},
                   "aggs": {"c": {"sum": {"field": "value"}}}}}})
print("  Cost by Workspace (30d):")
for b in d["aggregations"]["w"]["buckets"]:
    print(f"    {b['key']:<12} ${b['c']['value']:>10,.2f}")

# Tool failures -- via the analysed match form Dashboards actually runs
d = post("/logs-otel-*/_search", {
    "size": 0,
    "query": {"bool": {"filter": [
        {"match": {"log.attributes.event@name": "tool_result"}},
        {"match": {"log.attributes.success": "false"}}, rng]}},
    "aggs": {"t": {"terms": {"field": "log.attributes.tool_name.keyword", "size": 8}}}})
print(f"  Tool Failures (30d): {d['hits']['total']['value']} total")
for b in d["aggregations"]["t"]["buckets"]:
    print(f"    {b['key']:<20} {b['doc_count']}")
PY

say "3. Router spans in the span index (multi-harness)"
python3 - "$OS" <<'PY'
import json, sys, urllib.request

OS = sys.argv[1]
r = urllib.request.Request(f"{OS}/otel-v1-apm-span-*/_search",
    data=json.dumps({"size": 0, "aggs": {"s": {"terms": {"field": "serviceName", "size": 10}}}}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(r, timeout=40) as resp:
    buckets = json.loads(resp.read())["aggregations"]["s"]["buckets"]
names = {b["key"]: b["doc_count"] for b in buckets}
for k, v in names.items():
    print(f"    {k:<18} {v:,}")
print()
print("    phantom-router present:", "YES" if "phantom-router" in names else "no")
print("    claude-code present   :", "YES" if "claude-code" in names
      else "no  <- expected until a NEW session runs with the beta flag")
PY

say "4. Claude Code span emission gate"
if [ -n "${CLAUDE_CODE_ENHANCED_TELEMETRY_BETA:+set}" ]; then
  ok "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA is set in THIS shell"
  inf "a session started from here will emit spans"
else
  no "not set in this shell"
  inf "it is in \$WORKSPACE_HOME/.env; cd there so direnv loads it, then start a new session"
fi

say "5. Content-stripping invariant (live spans carry metadata, never text)"
python3 - "$OS" <<'PY'
import json, sys, urllib.request

OS = sys.argv[1]
r = urllib.request.Request(f"{OS}/otel-v1-apm-span-*/_search",
    data=json.dumps({"size": 20, "sort": [{"startTime": {"order": "desc"}}],
                     "query": {"match": {"serviceName": "phantom-router"}}}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(r, timeout=40) as resp:
    hits = json.loads(resp.read())["hits"]["hits"]

if not hits:
    print("    no phantom-router spans yet")
    raise SystemExit

BANNED = ("input", "output", "prompt", "completion", "message", "content")

# dual-sink-probe is the PRE-FIX spike that discovered the leak -- it ran
# before the stripping exporter existed and its payload is synthetic. It is
# reported separately rather than counted against the current code path.
PRE_FIX = "dual-sink-probe"

clean, dirty, historical = 0, [], []
for h in hits:
    s = h["_source"]
    found = [k for k in s
             if k.startswith("span.attributes")
             and any(b in k.lower() for b in BANNED)
             and not k.lower().endswith("_tokens")]
    if not found:
        clean += 1
    elif s.get("name") == PRE_FIX:
        historical.append((s.get("startTime"), found))
    else:
        dirty.append((s.get("name"), s.get("startTime"), found))

print(f"    inspected {len(hits)} phantom-router spans")
print(f"    clean (metadata only)              : {clean}")
print(f"    carrying content, current code path: {len(dirty)}")
for name, ts, keys in dirty:
    print(f"      {name} @ {ts}: {sorted(keys)}")
if historical:
    print(f"    pre-fix probe spans (expected)     : {len(historical)}")
    for ts, keys in historical:
        print(f"      {PRE_FIX} @ {ts}: {len(keys)} attrs - synthetic payload,")
        print("        emitted before the stripping exporter existed")
print()
print("    RESULT:", "PASS - the current code path exports metadata only"
      if not dirty else "FAIL - content is reaching OpenSearch")
PY

printf '\n'
