---
name: excalidraw
description: Excalidraw diagram syntax — hand-drawn style diagrams using Excalidraw's JSON format. Good for informal architecture sketches and wireframes with a whiteboard aesthetic. Requires companion container.
---

# Excalidraw

Excalidraw produces hand-drawn style diagrams — the kind that look like whiteboard sketches. Best for informal architecture drawings and wireframes where the "sketchy" aesthetic signals work-in-progress.

## Rendering

```
convert_diagram("excalidraw", source, "svg")
convert_diagram("excalidraw", source, "png")
```

**Requires companion container.**

## Source Format

Excalidraw source is JSON. The format is the same as Excalidraw's `.excalidraw` file format.

## Minimal Structure

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "https://excalidraw.com",
  "elements": [],
  "appState": {
    "gridSize": null,
    "viewBackgroundColor": "#ffffff"
  }
}
```

## Element Types

### Rectangle

```json
{
  "type": "rectangle",
  "id": "rect1",
  "x": 100, "y": 100,
  "width": 200, "height": 80,
  "strokeColor": "#1e1e1e",
  "backgroundColor": "#a5d8ff",
  "fillStyle": "hachure",
  "strokeWidth": 2,
  "roughness": 1,
  "opacity": 100,
  "roundness": {"type": 3}
}
```

### Text

```json
{
  "type": "text",
  "id": "text1",
  "x": 150, "y": 130,
  "width": 100, "height": 25,
  "text": "Web Server",
  "fontSize": 20,
  "fontFamily": 1,
  "textAlign": "center",
  "verticalAlign": "middle",
  "strokeColor": "#1e1e1e"
}
```

### Arrow

```json
{
  "type": "arrow",
  "id": "arrow1",
  "x": 300, "y": 140,
  "width": 100, "height": 0,
  "points": [[0, 0], [100, 0]],
  "strokeColor": "#1e1e1e",
  "strokeWidth": 2,
  "roughness": 1,
  "startArrowhead": null,
  "endArrowhead": "arrow",
  "startBinding": {
    "elementId": "rect1",
    "focus": 0,
    "gap": 8
  },
  "endBinding": {
    "elementId": "rect2",
    "focus": 0,
    "gap": 8
  }
}
```

### Line (no arrowhead)

```json
{
  "type": "line",
  "id": "line1",
  "x": 100, "y": 200,
  "width": 200, "height": 0,
  "points": [[0, 0], [200, 0]],
  "strokeColor": "#1e1e1e",
  "roughness": 1
}
```

### Ellipse / Circle

```json
{
  "type": "ellipse",
  "id": "ellipse1",
  "x": 100, "y": 100,
  "width": 120, "height": 120,
  "backgroundColor": "#b2f2bb",
  "strokeColor": "#1e1e1e"
}
```

### Diamond

```json
{
  "type": "diamond",
  "id": "diamond1",
  "x": 200, "y": 100,
  "width": 120, "height": 80,
  "backgroundColor": "#fff3bf",
  "strokeColor": "#1e1e1e"
}
```

## Style Options

| Attribute | Values |
|-----------|--------|
| `fillStyle` | `"hachure"` (hatched), `"cross-hatch"`, `"solid"`, `"zigzag"`, `"dots"`, `"none"` |
| `strokeStyle` | `"solid"`, `"dashed"`, `"dotted"` |
| `roughness` | `0` (smooth), `1` (normal sketch), `2` (extra rough) |
| `strokeWidth` | `1`, `2`, `4` |
| `opacity` | `0`–`100` |
| `fontFamily` | `1` (hand-drawn), `2` (normal), `3` (monospace) |
| `fontSize` | `16`, `20`, `28`, `36` |

## Complete Example: Simple Architecture

```json
{
  "type": "excalidraw",
  "version": 2,
  "elements": [
    {
      "type": "rectangle",
      "id": "browser",
      "x": 50, "y": 100,
      "width": 160, "height": 80,
      "strokeColor": "#1e1e1e",
      "backgroundColor": "#a5d8ff",
      "fillStyle": "hachure",
      "roughness": 1,
      "strokeWidth": 2
    },
    {
      "type": "text",
      "id": "browser_label",
      "x": 80, "y": 128,
      "width": 100, "height": 25,
      "text": "Browser",
      "fontSize": 20,
      "fontFamily": 1,
      "textAlign": "center",
      "strokeColor": "#1e1e1e"
    },
    {
      "type": "rectangle",
      "id": "api",
      "x": 300, "y": 100,
      "width": 160, "height": 80,
      "strokeColor": "#1e1e1e",
      "backgroundColor": "#b2f2bb",
      "fillStyle": "hachure",
      "roughness": 1,
      "strokeWidth": 2
    },
    {
      "type": "text",
      "id": "api_label",
      "x": 330, "y": 128,
      "width": 100, "height": 25,
      "text": "API",
      "fontSize": 20,
      "fontFamily": 1,
      "textAlign": "center",
      "strokeColor": "#1e1e1e"
    },
    {
      "type": "arrow",
      "id": "arrow1",
      "x": 210, "y": 140,
      "width": 90, "height": 0,
      "points": [[0, 0], [90, 0]],
      "strokeColor": "#1e1e1e",
      "strokeWidth": 2,
      "roughness": 1,
      "endArrowhead": "arrow"
    }
  ],
  "appState": {
    "viewBackgroundColor": "#ffffff"
  }
}
```

## Tips

- `roughness: 1` gives the characteristic hand-drawn look; `0` is smooth, `2` is very rough
- `fontFamily: 1` is the Excalidraw hand-drawn font; `2` is a normal sans-serif
- `fillStyle: "hachure"` gives the hatched fill that makes Excalidraw distinctive
- Coordinate system: top-left is `(0,0)`, x increases right, y increases down
- For complex diagrams, draw them in the Excalidraw web app and export the `.excalidraw` JSON, then render with Kroki
- Binding arrows to elements (`startBinding`/`endBinding`) makes them stay attached if you later move elements in the editor

## See Also
- `svgbob` skill — ASCII art with a similar informal feel, no JSON
- `ditaa` skill — ASCII art box diagrams
