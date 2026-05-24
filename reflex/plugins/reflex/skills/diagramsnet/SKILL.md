---
name: diagramsnet
description: diagrams.net (draw.io) XML diagram syntax — render draw.io diagrams via Kroki. Accepts the full draw.io XML format. Requires companion container.
---

# diagrams.net (draw.io)

Render draw.io / diagrams.net diagrams via Kroki. The source format is draw.io's native XML format.

## Rendering

```
convert_diagram("diagramsnet", source, "svg")
convert_diagram("diagramsnet", source, "png")
```

**Requires companion container.**

## Source Format

draw.io XML: a `<mxGraphModel>` containing `<root>` with `<mxCell>` elements.

## Minimal Structure

```xml
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>

    <!-- Elements go here, with parent="1" -->

  </root>
</mxGraphModel>
```

Every diagram needs cells with `id="0"` and `id="1"` as the graph root — don't omit them.

## Shapes (Vertices)

```xml
<!-- Rectangle -->
<mxCell id="2" value="Web Server" style="rounded=1;whiteSpace=wrap;" 
        vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="160" height="60" as="geometry"/>
</mxCell>

<!-- Ellipse -->
<mxCell id="3" value="Start" style="ellipse;whiteSpace=wrap;" 
        vertex="1" parent="1">
  <mxGeometry x="300" y="100" width="80" height="60" as="geometry"/>
</mxCell>

<!-- Diamond -->
<mxCell id="4" value="Valid?" style="rhombus;whiteSpace=wrap;" 
        vertex="1" parent="1">
  <mxGeometry x="200" y="220" width="120" height="80" as="geometry"/>
</mxCell>

<!-- Cylinder (database) -->
<mxCell id="5" value="Database" style="shape=mxgraph.flowchart.database;whiteSpace=wrap;"
        vertex="1" parent="1">
  <mxGeometry x="500" y="100" width="100" height="80" as="geometry"/>
</mxCell>
```

## Connections (Edges)

```xml
<!-- Simple arrow -->
<mxCell id="10" value="" style="edgeStyle=orthogonalEdgeStyle;" 
        edge="1" source="2" target="3" parent="1">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>

<!-- Labeled arrow -->
<mxCell id="11" value="HTTP/S" style="edgeStyle=orthogonalEdgeStyle;" 
        edge="1" source="2" target="5" parent="1">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>

<!-- Dashed arrow -->
<mxCell id="12" value="" style="dashed=1;edgeStyle=orthogonalEdgeStyle;" 
        edge="1" source="3" target="4" parent="1">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

## Common Style Attributes

```
rounded=1               rounded corners
fillColor=#dae8fc       fill color
strokeColor=#6c8ebf     border color
fontColor=#000000       text color
fontSize=14
fontStyle=1             bold (1=bold, 2=italic, 3=bold+italic)
align=center            text alignment (left, center, right)
verticalAlign=middle    (top, middle, bottom)
whiteSpace=wrap         allow text wrapping
dashed=1                dashed border
opacity=50              transparency (0-100)
```

## Complete Example: Three-Tier Architecture

```xml
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>

    <!-- Browser -->
    <mxCell id="browser" value="Browser" 
            style="rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=14;" 
            vertex="1" parent="1">
      <mxGeometry x="40" y="160" width="120" height="60" as="geometry"/>
    </mxCell>

    <!-- Load Balancer -->
    <mxCell id="lb" value="Load Balancer" 
            style="rounded=1;fillColor=#d5e8d4;strokeColor=#82b366;fontSize=14;" 
            vertex="1" parent="1">
      <mxGeometry x="240" y="160" width="120" height="60" as="geometry"/>
    </mxCell>

    <!-- App Servers container -->
    <mxCell id="app_group" value="App Tier" 
            style="swimlane;fillColor=#fff2cc;strokeColor=#d6b656;fontSize=14;" 
            vertex="1" parent="1">
      <mxGeometry x="440" y="100" width="160" height="180" as="geometry"/>
    </mxCell>

    <mxCell id="app1" value="App Server 1" 
            style="rounded=1;fillColor=#fff2cc;strokeColor=#d6b656;" 
            vertex="1" parent="app_group">
      <mxGeometry x="20" y="50" width="120" height="40" as="geometry"/>
    </mxCell>

    <mxCell id="app2" value="App Server 2" 
            style="rounded=1;fillColor=#fff2cc;strokeColor=#d6b656;" 
            vertex="1" parent="app_group">
      <mxGeometry x="20" y="110" width="120" height="40" as="geometry"/>
    </mxCell>

    <!-- Database -->
    <mxCell id="db" value="PostgreSQL" 
            style="shape=mxgraph.flowchart.database;fillColor=#f8cecc;strokeColor=#b85450;fontSize=14;" 
            vertex="1" parent="1">
      <mxGeometry x="680" y="140" width="100" height="80" as="geometry"/>
    </mxCell>

    <!-- Connections -->
    <mxCell id="e1" value="HTTPS" style="edgeStyle=orthogonalEdgeStyle;" 
            edge="1" source="browser" target="lb" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>

    <mxCell id="e2" value="" style="edgeStyle=orthogonalEdgeStyle;" 
            edge="1" source="lb" target="app_group" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>

    <mxCell id="e3" value="SQL" style="edgeStyle=orthogonalEdgeStyle;" 
            edge="1" source="app_group" target="db" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>

  </root>
</mxGraphModel>
```

## Swimlanes / Groups

```xml
<!-- Container / group -->
<mxCell id="container" value="Backend Services" 
        style="swimlane;fillColor=#f5f5f5;strokeColor=#666666;fontColor=#333333;" 
        vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="300" height="200" as="geometry"/>
</mxCell>

<!-- Child elements use parent="container" -->
<mxCell id="child" value="Service A" 
        style="rounded=1;" 
        vertex="1" parent="container">
  <mxGeometry x="20" y="50" width="120" height="50" as="geometry"/>
</mxCell>
```

## Tips

- Always include the two root cells (`id="0"` and `id="1"`) — the diagram won't render without them
- Use `edgeStyle=orthogonalEdgeStyle` for right-angle connections; `edgeStyle=elbowEdgeStyle` for step-style
- Child elements of containers use `parent="containerCellId"` and coordinates relative to the container
- The easiest workflow: draw in draw.io web app → File → Export as XML → render with Kroki
- Compressed (base64) XML is NOT supported — use uncompressed `<mxGraphModel>` XML only

## See Also
- `excalidraw` skill — hand-drawn style diagrams (also JSON/XML based)
- `plantuml` skill — text-based diagrams that are easier to write by hand
