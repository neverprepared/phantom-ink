---
name: data-viz-diagrams
description: Create data visualization charts — bar, line, scatter, area, pie, heatmap, histogram. Covers Vega-Lite (concise) and Vega (full control). Obsidian-kroki rendering.
---

# Data Visualization Diagrams

Charts and graphs from data — bar, line, scatter, area, pie, heatmap, histogram.

**Recommended:** Vega-Lite for standard charts (far less JSON). Vega for custom layouts or compositions Vega-Lite can't express.

## In Obsidian (obsidian-kroki)

Use `vegalite` or `vega` as the fenced code block language identifier — renders inline automatically.

## Vega-Lite — `vegalite`

Core structure: data → mark → encoding channels.

### Bar Chart

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "data": {"values": [
    {"month": "Jan", "revenue": 42000},
    {"month": "Feb", "revenue": 68000},
    {"month": "Mar", "revenue": 55000},
    {"month": "Apr", "revenue": 91000}
  ]},
  "mark": {"type": "bar", "color": "#3b82f6"},
  "encoding": {
    "x": {"field": "month",   "type": "nominal"},
    "y": {"field": "revenue", "type": "quantitative"}
  },
  "title": "Monthly Revenue"
}
```

### Line Chart (multi-series)

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": "line",
  "data": {"values": [
    {"date": "2024-01", "value": 100, "series": "A"},
    {"date": "2024-02", "value": 130, "series": "A"},
    {"date": "2024-01", "value": 80,  "series": "B"},
    {"date": "2024-02", "value": 95,  "series": "B"}
  ]},
  "encoding": {
    "x":     {"field": "date",   "type": "temporal"},
    "y":     {"field": "value",  "type": "quantitative"},
    "color": {"field": "series", "type": "nominal"}
  }
}
```

### Scatter Plot

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": "point",
  "data": {"values": [
    {"x": 1, "y": 2, "size": 10, "group": "A"},
    {"x": 3, "y": 5, "size": 20, "group": "B"}
  ]},
  "encoding": {
    "x":     {"field": "x",     "type": "quantitative"},
    "y":     {"field": "y",     "type": "quantitative"},
    "size":  {"field": "size",  "type": "quantitative"},
    "color": {"field": "group", "type": "nominal"}
  }
}
```

### Heatmap

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": "rect",
  "data": {"values": [
    {"day": "Mon", "hour": "9am",  "count": 12},
    {"day": "Mon", "hour": "10am", "count": 45},
    {"day": "Tue", "hour": "9am",  "count": 8}
  ]},
  "encoding": {
    "x":     {"field": "hour",  "type": "ordinal"},
    "y":     {"field": "day",   "type": "ordinal"},
    "color": {"field": "count", "type": "quantitative", "scale": {"scheme": "blues"}}
  }
}
```

### Pie / Donut

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": {"type": "arc", "innerRadius": 50},
  "data": {"values": [
    {"label": "Chrome", "share": 65},
    {"label": "Firefox","share": 15},
    {"label": "Safari", "share": 20}
  ]},
  "encoding": {
    "theta": {"field": "share", "type": "quantitative"},
    "color": {"field": "label", "type": "nominal"}
  }
}
```

Use `"innerRadius": 0` for pie, `50` for donut.

### Data types

| Type | When |
|------|------|
| `"nominal"` | Unordered categories (names, colors) |
| `"ordinal"` | Ordered categories (S/M/L, Mon/Tue/Wed) |
| `"quantitative"` | Continuous numbers |
| `"temporal"` | Dates and times |

### Aggregation

```json
"y": {"field": "value", "type": "quantitative", "aggregate": "sum"}
```
Options: `sum` · `mean` · `min` · `max` · `count` · `median` · `stdev`

### Layering (bars + text labels)

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "data": {"values": [{"x": "A", "y": 10}, {"x": "B", "y": 20}]},
  "layer": [
    {"mark": "bar", "encoding": {
      "x": {"field": "x", "type": "nominal"},
      "y": {"field": "y", "type": "quantitative"}
    }},
    {"mark": {"type": "text", "dy": -8}, "encoding": {
      "x": {"field": "x", "type": "nominal"},
      "y": {"field": "y", "type": "quantitative"},
      "text": {"field": "y", "type": "quantitative"}
    }}
  ]
}
```

## Vega — `vega`

Use when Vega-Lite can't express what you need — custom mark types, precise axis control, multi-view layouts.

```json
{
  "$schema": "https://vega.github.io/schema/vega/v5.json",
  "width": 500, "height": 300,
  "padding": {"left": 60, "right": 20, "top": 20, "bottom": 40},

  "data": [{"name": "table", "values": [
    {"x": "A", "y": 28}, {"x": "B", "y": 55}, {"x": "C", "y": 43}
  ]}],

  "scales": [
    {"name": "x", "type": "band",   "domain": {"data": "table", "field": "x"}, "range": "width", "padding": 0.1},
    {"name": "y", "type": "linear", "domain": {"data": "table", "field": "y"}, "range": "height", "nice": true}
  ],

  "axes": [
    {"orient": "bottom", "scale": "x"},
    {"orient": "left",   "scale": "y"}
  ],

  "marks": [{
    "type": "rect",
    "from": {"data": "table"},
    "encode": {"enter": {
      "x":     {"scale": "x", "field": "x"},
      "width": {"scale": "x", "band": 1},
      "y":     {"scale": "y", "field": "y"},
      "y2":    {"scale": "y", "value": 0},
      "fill":  {"value": "#3b82f6"}
    }}
  }]
}
```

## Choosing

| Need | Tool |
|------|------|
| Standard charts (bar, line, scatter, pie, heatmap) | Vega-Lite |
| Custom mark types, precise layout control | Vega |
