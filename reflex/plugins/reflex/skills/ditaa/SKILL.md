---
name: ditaa
description: ditaa diagram syntax — convert ASCII art box diagrams into clean rendered images. Best for simple architecture overviews and flow diagrams using ASCII characters.
---

# ditaa

ditaa (DIagrams Through Ascii Art) converts ASCII-art box diagrams into rendered images. Draw with characters, get clean output.

## Rendering

```
convert_diagram("ditaa", source, "svg")
convert_diagram("ditaa", source, "png")
```

No companion required.

## Core Syntax

Boxes are drawn with `+`, `-`, `|`. Arrows use `>`, `<`, `^`, `v`. Lines connect boxes automatically.

```
+--------+   +-------+   +----------+
|        |   |       |   |          |
| Client +-->| Proxy +-->|  Server  |
|        |   |       |   |          |
+--------+   +-------+   +----------+
```

## Box Styles

```
/--------\   Rounded corners (use / and \)
|  Round |
\--------/

+--------+   Sharp corners (default)
| Sharp  |
+--------+

/--------+   Mix as needed
| Mixed  |
+--------/
```

## Color Tags

```
+------------------+
|{c} Cyan box      |
+------------------+

+------------------+
|{g} Green box     |
+------------------+

+------------------+
|{r} Red box       |
+------------------+

+------------------+
|{o} Orange/yellow |
+------------------+

+------------------+
|{p} Pink          |
+------------------+
```

Color tag goes inside the box (first non-space characters): `{c}`, `{g}`, `{r}`, `{o}`, `{p}`, `{y}`, `{m}`, `{s}` (dark), `{d}` (dark grey)

## Arrows and Lines

```
Horizontal:    -->   or   <--   or   <-->

Vertical:      |         lines become arrows
               v         when they hit a box edge

Diagonal:      not supported

Dashed line:   - - - -  (spaces in the dash line)
```

```
+-------+         +-------+
|       |         |       |
|   A   +-------->|   B   |
|       |         |       |
+-------+         +-------+
    |
    |
    v
+-------+
|   C   |
+-------+
```

## Storage / Database Shape

```
/--------\
|  {s}   |   Storage shape: rounded top + {s} tag
|Database|
\--------/
```

## Document Shape

```
/--------\
|  {d}   |   Document shape
|  Doc   |
\--------/
```

## Example: System Architecture

```
                  +-------------+
                  |   Browser   |
                  +------+------+
                         |
                         v
/-----------\    +-------+-------+    /----------\
|           |    |               |    |          |
|  {g}CDN   +<-->|  Load Balanc  |--->| {c}Cache |
|           |    |               |    |          |
\-----------/    +---+-------+---+    \----------/
                     |       |
              +------+       +------+
              |                     |
              v                     v
        +-----+----+         +------+---+
        |  {o}     |         |  {o}     |
        | App Srv1 |         | App Srv2 |
        |          |         |          |
        +-----+----+         +------+---+
              |                     |
              +----------+----------+
                         |
                         v
                  /------+------\
                  |  {s}        |
                  |  Database   |
                  \-------------/
```

## Tips

- Keep boxes aligned — ditaa is whitespace-sensitive
- Boxes must have closed corners (`+` at each corner)
- Lines between boxes must be connected (touch the box edge)
- Use `{skip}` tag to render a box as transparent/invisible
- Lines that don't start/end at a box corner are just decorative
- `--` (double dash) creates a line without an arrowhead

## Limitations

- No diagonal connections
- No text on arrows (use separate labels near the line)
- Limited shapes: box, rounded box, storage, document
- No nested boxes

## See Also
- `svgbob` skill — richer ASCII art with more shapes and line styles
- `graphviz-diagrams` skill — when you need precise layout control
