---
name: sketch-diagrams
description: Create informal, hand-drawn style diagrams and import draw.io diagrams. Covers Excalidraw (whiteboard aesthetic) and diagrams.net/draw.io XML format. Obsidian-kroki rendering.
---

# Sketch Diagrams

Informal diagrams with a hand-drawn or drag-and-drop aesthetic — good for early-stage design, whiteboard captures, and importing from draw.io.

**Recommended:** excalidraw for fresh hand-drawn style diagrams. diagramsnet for rendering existing draw.io files.

Both require companion containers.

## In Obsidian (obsidian-kroki)

Use a fenced code block with the type as language identifier — renders inline automatically.

## Excalidraw — `excalidraw` (companion required)

Best for: informal, whiteboard-style diagrams where the sketchy aesthetic signals work-in-progress or early design.

Source is JSON (same as `.excalidraw` file format). The `roughness` and `fillStyle` properties produce the hand-drawn look.

```json
{
  "type": "excalidraw",
  "version": 2,
  "elements": [
    {
      "type": "rectangle",
      "id": "browser",
      "x": 50, "y": 100, "width": 160, "height": 80,
      "strokeColor": "#1e1e1e",
      "backgroundColor": "#a5d8ff",
      "fillStyle": "hachure",
      "roughness": 1,
      "strokeWidth": 2
    },
    {
      "type": "text",
      "id": "browser_label",
      "x": 90, "y": 130,
      "width": 80, "height": 25,
      "text": "Browser",
      "fontSize": 20, "fontFamily": 1,
      "strokeColor": "#1e1e1e"
    },
    {
      "type": "rectangle",
      "id": "api",
      "x": 300, "y": 100, "width": 160, "height": 80,
      "strokeColor": "#1e1e1e",
      "backgroundColor": "#b2f2bb",
      "fillStyle": "hachure",
      "roughness": 1,
      "strokeWidth": 2
    },
    {
      "type": "text",
      "id": "api_label",
      "x": 355, "y": 130,
      "width": 50, "height": 25,
      "text": "API",
      "fontSize": 20, "fontFamily": 1,
      "strokeColor": "#1e1e1e"
    },
    {
      "type": "arrow",
      "id": "arrow1",
      "x": 210, "y": 140, "width": 90, "height": 0,
      "points": [[0, 0], [90, 0]],
      "strokeColor": "#1e1e1e",
      "strokeWidth": 2, "roughness": 1,
      "endArrowhead": "arrow"
    }
  ],
  "appState": {"viewBackgroundColor": "#ffffff"}
}
```

Element types: `rectangle` · `ellipse` · `diamond` · `text` · `arrow` · `line`

Key style properties:
| Property | Values |
|----------|--------|
| `fillStyle` | `"hachure"` (hatched, signature look) · `"cross-hatch"` · `"solid"` · `"dots"` · `"none"` |
| `roughness` | `0` smooth · `1` normal sketch · `2` very rough |
| `strokeStyle` | `"solid"` · `"dashed"` · `"dotted"` |
| `fontFamily` | `1` hand-drawn · `2` normal · `3` monospace |
| `strokeWidth` | `1` · `2` · `4` |

Coordinate system: top-left is `(0,0)`, x right, y down.

**Tip:** For anything beyond a few elements, draw in [excalidraw.com](https://excalidraw.com), export as `.excalidraw`, then paste the JSON into the code block.

## diagrams.net — `diagramsnet` (companion required)

Best for: rendering existing draw.io diagrams in Obsidian without leaving the vault. Source is draw.io XML.

**Important:** Kroki requires **uncompressed XML** — not the default compressed format draw.io exports. In draw.io: Extras → Edit Diagram → copy the raw XML.

```xml
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>

    <mxCell id="browser" value="Browser"
            style="rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"
            vertex="1" parent="1">
      <mxGeometry x="40" y="160" width="120" height="60" as="geometry"/>
    </mxCell>

    <mxCell id="api" value="API"
            style="rounded=1;fillColor=#d5e8d4;strokeColor=#82b366;"
            vertex="1" parent="1">
      <mxGeometry x="240" y="160" width="120" height="60" as="geometry"/>
    </mxCell>

    <mxCell id="db" value="Database"
            style="shape=mxgraph.flowchart.database;fillColor=#fff2cc;strokeColor=#d6b656;"
            vertex="1" parent="1">
      <mxGeometry x="440" y="140" width="100" height="80" as="geometry"/>
    </mxCell>

    <mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;"
            edge="1" source="browser" target="api" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    <mxCell id="e2" value="SQL" style="edgeStyle=orthogonalEdgeStyle;"
            edge="1" source="api" target="db" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>
```

Always include `id="0"` and `id="1"` root cells — the diagram won't render without them.

Common style attributes: `rounded=1` · `fillColor=#hex` · `strokeColor=#hex` · `dashed=1` · `fontSize=14` · `fontStyle=1` (bold)  
Edge styles: `edgeStyle=orthogonalEdgeStyle` (right angles) · `edgeStyle=elbowEdgeStyle` (step)

## Choosing

| Need | Tool |
|------|------|
| Fresh hand-drawn whiteboard diagram | excalidraw |
| Render an existing draw.io file | diagramsnet |
