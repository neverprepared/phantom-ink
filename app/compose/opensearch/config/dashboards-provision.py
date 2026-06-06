#!/usr/bin/env python3
"""
Provision OpenSearch Dashboards index patterns, visualisations, and the
Claude Code observability dashboard.  Idempotent — 409 = already exists.
"""
import json, urllib.request, urllib.error, sys

BASE = "http://opensearch-dashboards:5601"
INDEX_REF_NAME = "kibanaSavedObjectMeta.searchSourceJSON.index"


def _req(method, path, body=None, ignore=(409,)):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"osd-xsrf": "true"}
    if data:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in ignore:
            return e.code, {}
        print(f"  ! {method} {path} -> {e.code}: {e.read().decode()[:400]}", file=sys.stderr)
        return e.code, {}


def save(type_, id_, attrs, refs=None, migration=None):
    body = {"attributes": attrs, "references": refs or []}
    if migration:
        body["migrationVersion"] = migration
    status, _ = _req("POST", f"/api/saved_objects/{type_}/{id_}", body)
    label = f"{type_}/{id_}"
    print(f"  {label:45s} -> {status}")
    return status


def delete(type_, id_):
    status, _ = _req("DELETE", f"/api/saved_objects/{type_}/{id_}",
                     ignore=(404, 409))
    return status


def refresh_index_pattern(id_, pattern, time_field):
    """Fetch field list from the live index and write it back into the
    index-pattern saved object so visualisations can reference all fields."""
    status, data = _req(
        "GET",
        f"/api/index_patterns/_fields_for_wildcard?pattern={pattern}"
        "&meta_fields=_source&meta_fields=_id&meta_fields=_index&meta_fields=_score",
        ignore=(404,))
    if status != 200 or "fields" not in data:
        print(f"  fields refresh for {id_:33s} -> {status} (no data yet)")
        return
    fields_json = json.dumps(data["fields"])
    body = {"attributes": {"title": pattern, "timeFieldName": time_field,
                           "fields": fields_json}}
    s, _ = _req("PUT", f"/api/saved_objects/index-pattern/{id_}", body)
    print(f"  fields refresh for {id_:33s} -> {s} ({len(data['fields'])} fields)")


# ── Wipe existing objects so this is a true recreate ───────────────────────────

print("==> Deleting existing objects")
for vis_id in ["cc-total-cost", "cc-active-time", "cc-cost-over-time",
               "cc-token-usage", "cc-api-requests", "cc-api-latency",
               "cc-model-cost"]:
    delete("visualization", vis_id)
delete("dashboard", "cc-dashboard")
for ip_id in ["logs-otel", "metrics-otel", "otel-apm-span"]:
    delete("index-pattern", ip_id)
print("  done")


# ── Index Patterns ─────────────────────────────────────────────────────────────

print("==> Index patterns")
save("index-pattern", "logs-otel",
     {"title": "logs-otel-*", "timeFieldName": "time"},
     migration={"index-pattern": "7.0.0"})
save("index-pattern", "metrics-otel",
     {"title": "metrics-otel-*", "timeFieldName": "time"},
     migration={"index-pattern": "7.0.0"})
save("index-pattern", "otel-apm-span",
     {"title": "otel-v1-apm-span-*", "timeFieldName": "startTime"},
     migration={"index-pattern": "7.0.0"})

refresh_index_pattern("logs-otel",     "logs-otel-*",        "time")
refresh_index_pattern("metrics-otel",  "metrics-otel-*",     "time")
refresh_index_pattern("otel-apm-span", "otel-v1-apm-span-*", "startTime")

_req("POST", "/api/opensearch-dashboards/settings",
     {"changes": {"defaultIndex": "logs-otel"}})
print(f"  {'default index':45s} -> logs-otel")


# ── Visualisation helpers ──────────────────────────────────────────────────────

def ssj(index_ref, query=""):
    return json.dumps({
        "indexRefName": INDEX_REF_NAME,
        "query": {"query": query, "language": "kuery"},
        "filter": [],
    })


def idx_ref(index_ref):
    return [{"id": index_ref, "name": INDEX_REF_NAME, "type": "index-pattern"}]


def vis(id_, title, type_, params, aggs, index_ref, query=""):
    attrs = {
        "title": title,
        "visState": json.dumps({"title": title, "type": type_,
                                "params": params, "aggs": aggs}),
        "uiStateJSON": "{}",
        "description": "",
        "version": 1,
        "kibanaSavedObjectMeta": {"searchSourceJSON": ssj(index_ref, query)},
    }
    save("visualization", id_, attrs, idx_ref(index_ref),
         migration={"visualization": "7.10.0"})


def cat_axes():
    return [{"id": "CategoryAxis-1", "type": "category", "position": "bottom",
             "show": True, "scale": {"type": "linear"},
             "labels": {"show": True, "filter": True, "truncate": 100}, "title": {}}]


def val_axes(label):
    return [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value",
             "position": "left", "show": True,
             "scale": {"type": "linear", "mode": "normal"},
             "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
             "title": {"text": label}}]


def date_hist(field="time"):
    return {"id": "2", "enabled": True, "type": "date_histogram",
            "params": {"field": field, "useNormalizedOpenSearchInterval": True,
                       "interval": "auto", "drop_partials": False,
                       "min_doc_count": 1, "extended_bounds": {}},
            "schema": "segment"}


def line_params(y_label):
    return {
        "type": "line", "grid": {"categoryLines": False},
        "categoryAxes": cat_axes(), "valueAxes": val_axes(y_label),
        "seriesParams": [{"show": True, "type": "line", "mode": "normal",
                          "data": {"label": y_label, "id": "1"},
                          "valueAxis": "ValueAxis-1",
                          "drawLinesBetweenPoints": True, "lineWidth": 2,
                          "showCircles": True}],
        "addTooltip": True, "addLegend": True, "legendPosition": "right",
        "times": [], "addTimeMarker": False,
    }


def bar_params(y_label, mode="stacked"):
    return {
        "type": "histogram", "grid": {"categoryLines": False},
        "categoryAxes": cat_axes(), "valueAxes": val_axes(y_label),
        "seriesParams": [{"show": True, "type": "histogram", "mode": mode,
                          "data": {"label": y_label, "id": "1"},
                          "valueAxis": "ValueAxis-1"}],
        "addTooltip": True, "addLegend": True, "legendPosition": "right",
        "times": [], "addTimeMarker": False,
    }


def metric_params(sub_text=""):
    return {
        "addTooltip": True, "addLegend": False, "type": "metric",
        "metric": {
            "percentageMode": False, "useRanges": False,
            "colorSchema": "Green to Red", "metricColorMode": "None",
            "colorsRange": [{"from": 0, "to": 10000}],
            "labels": {"show": True}, "invertColors": False,
            "style": {"bgFill": "#000", "bgColor": False, "labelColor": False,
                      "subText": sub_text, "fontSize": 60},
        },
    }


def sum_agg(field, label):
    return {"id": "1", "enabled": True, "type": "sum",
            "params": {"field": field, "customLabel": label}, "schema": "metric"}


def count_agg(label):
    return {"id": "1", "enabled": True, "type": "count",
            "params": {"customLabel": label}, "schema": "metric"}


def avg_agg(field, label):
    return {"id": "1", "enabled": True, "type": "avg",
            "params": {"field": field, "customLabel": label}, "schema": "metric"}


def terms_agg(field, label, size=10):
    return {"id": "3", "enabled": True, "type": "terms",
            "params": {"field": field, "size": size, "order": "desc",
                       "orderBy": "1", "customLabel": label},
            "schema": "group"}


# ── Visualisations ─────────────────────────────────────────────────────────────

print("==> Visualisations")

vis("cc-total-cost", "Total Cost (USD)", "metric",
    metric_params("USD"),
    [sum_agg("value", "Cost (USD)")],
    "metrics-otel", 'name: "claude_code.cost.usage"')

vis("cc-active-time", "Active Time (sec)", "metric",
    metric_params("sec"),
    [sum_agg("value", "Active Time")],
    "metrics-otel", 'name: "claude_code.active_time.total"')

vis("cc-cost-over-time", "Cost Over Time", "line",
    line_params("USD"),
    [sum_agg("value", "Cost (USD)"), date_hist()],
    "metrics-otel", 'name: "claude_code.cost.usage"')

vis("cc-token-usage", "Token Usage by Type", "histogram",
    bar_params("Tokens"),
    [sum_agg("value", "Tokens"), date_hist(),
     terms_agg("metric.attributes.type.keyword", "Type", size=6)],
    "metrics-otel", 'name: "claude_code.token.usage"')

vis("cc-api-requests", "API Requests by Model", "histogram",
    bar_params("Requests", mode="grouped"),
    [count_agg("Requests"), date_hist(),
     terms_agg("log.attributes.model.keyword", "Model", size=5)],
    "logs-otel", 'log.attributes.event@name: "api_request"')

vis("cc-api-latency", "API Latency (avg ms)", "line",
    line_params("ms"),
    [avg_agg("log.attributes.duration_ms", "Avg Latency (ms)"), date_hist()],
    "logs-otel", 'log.attributes.event@name: "api_request"')

vis("cc-model-cost", "Cost by Model", "pie",
    {"type": "pie", "addTooltip": True, "addLegend": True,
     "legendPosition": "right", "isDonut": True,
     "labels": {"show": False, "values": True, "last_level": True, "truncate": 100}},
    [sum_agg("value", "Cost (USD)"),
     {"id": "2", "enabled": True, "type": "terms",
      "params": {"field": "metric.attributes.model.keyword", "size": 10,
                 "order": "desc", "orderBy": "1", "customLabel": "Model"},
      "schema": "segment"}],
    "metrics-otel", 'name: "claude_code.cost.usage"')


# ── Dashboard ──────────────────────────────────────────────────────────────────

print("==> Dashboard")

# Grid is 48 columns wide; each height unit ≈ 20 px.
# (vis_id,            x,   y,   w,  h)
layout = [
    ("cc-total-cost",      0,   0,  12,  5),
    ("cc-active-time",    12,   0,  12,  5),
    ("cc-model-cost",     24,   0,  24, 15),
    ("cc-cost-over-time",  0,   5,  24, 10),
    ("cc-token-usage",     0,  15,  24, 10),
    ("cc-api-requests",   24,  15,  24, 10),
    ("cc-api-latency",     0,  25,  48, 10),
]

panels, refs = [], []
for i, (vis_id, x, y, w, h) in enumerate(layout, 1):
    ref = f"panel_{i}"
    panels.append({"embeddableConfig": {}, "panelIndex": str(i),
                   "panelRefName": ref, "type": "visualization",
                   "gridData": {"x": x, "y": y, "w": w, "h": h, "i": str(i)}})
    refs.append({"id": vis_id, "name": ref, "type": "visualization"})

dash_attrs = {
    "title": "Claude Code",
    "description": "Claude Code usage, cost, tokens, and API performance",
    "hits": 0,
    "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(
        {"query": {"language": "kuery", "query": ""}, "filter": []})},
    "optionsJSON": json.dumps({"useMargins": True, "hidePanelTitles": False}),
    "panelsJSON": json.dumps(panels),
    "refreshInterval": {"pause": True, "value": 0},
    "timeFrom": "now-24h", "timeTo": "now", "timeRestore": True,
    "version": 1,
}
save("dashboard", "cc-dashboard", dash_attrs, refs,
     migration={"dashboard": "7.9.3"})


# ── Observability Application ──────────────────────────────────────────────────

print("==> Observability application")
status, _ = _req("POST", "/api/observability/application/", {
    "name": "Claude Code",
    "description": "Claude Code traces, metrics, and logs",
    "baseQuery": "",
    "servicesEntities": [],
    "traceGroups": [],
})
print(f"  {'observability/application':45s} -> {status}")

print("\n==> Done")
print("  Dashboard: http://localhost:5601/app/dashboards#/view/cc-dashboard")
