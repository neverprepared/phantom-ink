---
name: vegalite
description: Vega-Lite diagram syntax — concise JSON grammar for standard data visualizations (bar, line, scatter, area, pie, heatmap, histogram). Much simpler than full Vega.
---

# Vega-Lite

Vega-Lite is a high-level grammar for common charts. Write far less JSON than Vega for standard visualizations.

## Rendering

```
convert_diagram("vegalite", source, "svg")
convert_diagram("vegalite", source, "png")
```

No companion required. Source is JSON.

## Core Structure

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "width": 400,
  "height": 300,
  "data": {"values": [...]},
  "mark": "bar",
  "encoding": {
    "x": {"field": "category", "type": "nominal"},
    "y": {"field": "value",    "type": "quantitative"}
  }
}
```

## Data Types

| Type | When to Use |
|------|-------------|
| `"nominal"` | Categorical, unordered (colors, names) |
| `"ordinal"` | Categorical, ordered (sizes: S/M/L) |
| `"quantitative"` | Numeric (continuous) |
| `"temporal"` | Dates and times |

## Bar Chart

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "data": {
    "values": [
      {"month": "Jan", "revenue": 42000},
      {"month": "Feb", "revenue": 68000},
      {"month": "Mar", "revenue": 55000},
      {"month": "Apr", "revenue": 91000},
      {"month": "May", "revenue": 73000}
    ]
  },
  "mark": {"type": "bar", "color": "#3b82f6"},
  "encoding": {
    "x": {"field": "month", "type": "nominal", "axis": {"title": "Month"}},
    "y": {"field": "revenue", "type": "quantitative", "axis": {"title": "Revenue ($)"}},
    "tooltip": [
      {"field": "month", "type": "nominal"},
      {"field": "revenue", "type": "quantitative", "format": "$,.0f"}
    ]
  },
  "title": "Monthly Revenue"
}
```

## Line Chart

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "data": {
    "values": [
      {"date": "2024-01-01", "value": 100, "series": "A"},
      {"date": "2024-02-01", "value": 130, "series": "A"},
      {"date": "2024-01-01", "value": 80,  "series": "B"},
      {"date": "2024-02-01", "value": 95,  "series": "B"}
    ]
  },
  "mark": "line",
  "encoding": {
    "x": {"field": "date", "type": "temporal"},
    "y": {"field": "value", "type": "quantitative"},
    "color": {"field": "series", "type": "nominal"}
  }
}
```

## Scatter Plot

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "data": {
    "values": [
      {"x": 1, "y": 2, "size": 10, "group": "A"},
      {"x": 3, "y": 5, "size": 20, "group": "B"},
      {"x": 5, "y": 3, "size": 15, "group": "A"}
    ]
  },
  "mark": "point",
  "encoding": {
    "x":     {"field": "x",     "type": "quantitative"},
    "y":     {"field": "y",     "type": "quantitative"},
    "size":  {"field": "size",  "type": "quantitative"},
    "color": {"field": "group", "type": "nominal"}
  }
}
```

## Area Chart

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": {"type": "area", "opacity": 0.7},
  "data": {"values": [
    {"t": "2024-01", "v": 10},
    {"t": "2024-02", "v": 30},
    {"t": "2024-03", "v": 25}
  ]},
  "encoding": {
    "x": {"field": "t", "type": "temporal"},
    "y": {"field": "v", "type": "quantitative"}
  }
}
```

## Heatmap

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": "rect",
  "data": {"values": [
    {"day": "Mon", "hour": "9am",  "count": 12},
    {"day": "Mon", "hour": "10am", "count": 45},
    {"day": "Tue", "hour": "9am",  "count": 8},
    {"day": "Tue", "hour": "10am", "count": 33}
  ]},
  "encoding": {
    "x":     {"field": "hour", "type": "ordinal"},
    "y":     {"field": "day",  "type": "ordinal"},
    "color": {"field": "count", "type": "quantitative", "scale": {"scheme": "blues"}}
  }
}
```

## Histogram

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": "bar",
  "data": {"values": [
    {"value": 2}, {"value": 7}, {"value": 4}, {"value": 9}, {"value": 3}
  ]},
  "encoding": {
    "x": {"field": "value", "type": "quantitative", "bin": {"maxbins": 10}},
    "y": {"aggregate": "count", "type": "quantitative"}
  }
}
```

## Pie / Arc Chart

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": {"type": "arc", "innerRadius": 0},
  "data": {"values": [
    {"label": "Chrome",  "share": 65},
    {"label": "Firefox", "share": 15},
    {"label": "Safari",  "share": 12},
    {"label": "Other",   "share": 8}
  ]},
  "encoding": {
    "theta": {"field": "share", "type": "quantitative"},
    "color": {"field": "label", "type": "nominal"}
  }
}
```

Use `"innerRadius": 50` for a donut chart.

## Layered Chart (Multi-mark)

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "data": {"values": [
    {"x": "A", "y": 10}, {"x": "B", "y": 20}, {"x": "C", "y": 15}
  ]},
  "layer": [
    {
      "mark": "bar",
      "encoding": {
        "x": {"field": "x", "type": "nominal"},
        "y": {"field": "y", "type": "quantitative"}
      }
    },
    {
      "mark": {"type": "text", "dy": -5},
      "encoding": {
        "x": {"field": "x", "type": "nominal"},
        "y": {"field": "y", "type": "quantitative"},
        "text": {"field": "y", "type": "quantitative"}
      }
    }
  ]
}
```

## Faceted / Small Multiples

```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "data": {"values": [...]},
  "facet": {"field": "category", "type": "nominal", "columns": 2},
  "spec": {
    "mark": "line",
    "encoding": {
      "x": {"field": "date", "type": "temporal"},
      "y": {"field": "value", "type": "quantitative"}
    }
  }
}
```

## Common Encoding Channels

| Channel | Purpose |
|---------|---------|
| `x`, `y` | Position |
| `color` | Color by field |
| `size` | Size by field |
| `shape` | Shape by field |
| `opacity` | Transparency |
| `text` | Text labels |
| `tooltip` | Hover labels |
| `detail` | Group without visual encoding |

## Aggregations

```json
"y": {"field": "value", "type": "quantitative", "aggregate": "sum"}
// Other: mean, min, max, count, median, stdev, variance
```

## Tips

- Always set `"$schema"` — it controls which version of the grammar is used
- `"nominal"` for unordered categories, `"ordinal"` for ordered ones (this matters for axis sorting)
- Use `"layer"` to combine marks (e.g., bars + text labels)
- `"width": "container"` makes the chart responsive to its container
- For complex multi-layer compositions Vega-Lite can't express, step up to `vega`

## See Also
- `vega` skill — full Vega grammar for custom/complex charts
