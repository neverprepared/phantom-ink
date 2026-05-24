---
name: vega
description: Vega visualization grammar — full declarative JSON grammar for complex data visualizations including custom charts, interactions, and multi-view layouts. Renders via phantom-diagrams MCP.
---

# Vega

Vega is a full declarative visualization grammar for building complex, custom charts and interactive graphics from data. More powerful and verbose than Vega-Lite.

## Rendering

```
convert_diagram("vega", source, "svg")
convert_diagram("vega", source, "png")
```

No companion required. Source is JSON.

## Core Structure

```json
{
  "$schema": "https://vega.github.io/schema/vega/v5.json",
  "width": 500,
  "height": 300,
  "padding": 5,

  "data": [...],
  "scales": [...],
  "axes": [...],
  "marks": [...]
}
```

## Data Sources

```json
"data": [
  {
    "name": "table",
    "values": [
      {"category": "A", "amount": 28},
      {"category": "B", "amount": 55},
      {"category": "C", "amount": 43}
    ]
  },
  {
    "name": "aggregated",
    "source": "table",
    "transform": [
      {"type": "aggregate", "fields": ["amount"], "ops": ["sum"], "as": ["total"]}
    ]
  }
]
```

## Scales

```json
"scales": [
  {
    "name": "xscale",
    "type": "band",
    "domain": {"data": "table", "field": "category"},
    "range": "width",
    "padding": 0.1
  },
  {
    "name": "yscale",
    "type": "linear",
    "domain": {"data": "table", "field": "amount"},
    "range": "height",
    "nice": true
  },
  {
    "name": "color",
    "type": "ordinal",
    "domain": {"data": "table", "field": "category"},
    "range": {"scheme": "category10"}
  }
]
```

## Axes

```json
"axes": [
  {"orient": "bottom", "scale": "xscale", "title": "Category"},
  {"orient": "left",   "scale": "yscale", "title": "Amount"}
]
```

## Marks

```json
"marks": [
  {
    "type": "rect",
    "from": {"data": "table"},
    "encode": {
      "enter": {
        "x":     {"scale": "xscale", "field": "category"},
        "width": {"scale": "xscale", "band": 1},
        "y":     {"scale": "yscale", "field": "amount"},
        "y2":    {"scale": "yscale", "value": 0},
        "fill":  {"scale": "color", "field": "category"}
      }
    }
  },
  {
    "type": "text",
    "from": {"data": "table"},
    "encode": {
      "enter": {
        "x":      {"scale": "xscale", "field": "category", "band": 0.5},
        "y":      {"scale": "yscale", "field": "amount", "offset": -5},
        "align":  {"value": "center"},
        "text":   {"field": "amount"}
      }
    }
  }
]
```

Mark types: `rect`, `symbol`, `line`, `area`, `text`, `arc`, `path`, `rule`, `image`, `group`

## Complete Example: Bar Chart

```json
{
  "$schema": "https://vega.github.io/schema/vega/v5.json",
  "width": 500,
  "height": 300,
  "padding": {"left": 60, "right": 20, "top": 20, "bottom": 40},

  "data": [{
    "name": "table",
    "values": [
      {"x": "Jan", "y": 42},
      {"x": "Feb", "y": 68},
      {"x": "Mar", "y": 55},
      {"x": "Apr", "y": 91},
      {"x": "May", "y": 73}
    ]
  }],

  "scales": [
    {
      "name": "x",
      "type": "band",
      "domain": {"data": "table", "field": "x"},
      "range": "width",
      "padding": 0.15
    },
    {
      "name": "y",
      "type": "linear",
      "domain": [0, {"data": "table", "field": "y", "op": "max"}],
      "range": "height",
      "nice": true
    }
  ],

  "axes": [
    {"orient": "bottom", "scale": "x"},
    {"orient": "left",   "scale": "y", "tickCount": 5}
  ],

  "marks": [{
    "type": "rect",
    "from": {"data": "table"},
    "encode": {
      "enter": {
        "x":      {"scale": "x", "field": "x"},
        "width":  {"scale": "x", "band": 1},
        "y":      {"scale": "y", "field": "y"},
        "y2":     {"scale": "y", "value": 0},
        "fill":   {"value": "#3b82f6"},
        "tooltip": {"signal": "datum.x + ': ' + datum.y"}
      }
    }
  }]
}
```

## Transforms

```json
"transform": [
  {"type": "filter",    "expr": "datum.value > 0"},
  {"type": "sort",      "field": "value", "order": "descending"},
  {"type": "window",    "ops": ["rank"], "as": ["rank"]},
  {"type": "aggregate", "groupby": ["category"], "fields": ["value"], "ops": ["sum"], "as": ["total"]},
  {"type": "formula",   "as": "label", "expr": "datum.value + '%'"},
  {"type": "fold",      "fields": ["a", "b", "c"], "as": ["key", "value"]},
  {"type": "flatten",   "fields": ["tags"]},
  {"type": "bin",       "field": "value", "extent": [0, 100], "step": 10}
]
```

## Color Schemes

Named schemes for `"range": {"scheme": "..."}`:
- Categorical: `category10`, `category20`, `tableau10`, `set1`, `set2`, `set3`
- Sequential: `blues`, `greens`, `reds`, `oranges`, `purples`, `greys`
- Diverging: `redblue`, `redyellowblue`, `spectral`

## Vega vs Vega-Lite

| Feature | Vega | Vega-Lite |
|---------|------|-----------|
| Expressiveness | Full control | Concise shorthand |
| Verbosity | High | Low |
| Custom layouts | Yes | Limited |
| Multi-view | Yes (group marks) | Yes (facet/layer) |
| Learning curve | Steep | Gentle |
| Best for | Custom, complex charts | Standard charts quickly |

**Use Vega when:** You need precise control, custom mark types, or multi-layer compositions that Vega-Lite can't express.
**Use Vega-Lite when:** You need standard charts fast.

## See Also
- `vegalite` skill — simpler Vega-Lite for standard charts
