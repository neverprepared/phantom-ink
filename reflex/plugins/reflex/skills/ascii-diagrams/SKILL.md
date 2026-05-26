---
name: ascii-diagrams
description: Create diagrams from ASCII art — svgbob (rich ASCII to SVG), ditaa (colored box diagrams), and pikchr (precise geometric drawings). All render without companion containers.
---

# ASCII Diagrams

Draw diagrams using ASCII characters — no external tools needed, source is human-readable in plain text.

**Recommended:** svgbob for most ASCII diagrams (richer line support). ditaa when you want color-coded boxes. pikchr when you need precise geometric shapes.

## In Obsidian (obsidian-kroki)

Use a fenced code block with the type as language identifier — renders inline automatically.

## svgbob — `svgbob`

Best for: architecture sketches, flowcharts, sequence-style diagrams in ASCII. Produces clean SVG from box-drawing characters.

```
.----------.     .---------.     .----------.
|          |     |         |     |          |
|  Client  +---->|  Proxy  +---->|  Server  |
|          |     |         |     |          |
'----------'     '---------'     '----------'
                      |
                      v
                 .---------.
                 |  Logger |
                 '---------'
```

Sequence-style:
```
Client          API           DB
  |              |             |
  +--request---->|             |
  |              +---query---->|
  |              |<--results---+
  |<--response---+             |
```

State machine:
```
           .---------.
    .------>|  Idle   |<-------.
    |       '---------'        |
    |            |             |
    |         fetch()          |
    |            v             |
    |       .---------.        |
    |       | Loading |        |
    |       '-+-----+-'        |
    |         |     |          |
    |       200     error      |
    |         v     v          |
    |     .------. .-------.   |
    |     |  OK  | | Error |   |
    '-----'------' '---+---'   |
                       '-------'
```

Key characters: `+` corner · `-` horizontal · `|` vertical · `/` `\` diagonal · `>` `<` `^` `v` arrowheads · `.` `'` rounded corners · `*` dot · `o` open circle

## ditaa — `ditaa`

Best for: box-and-arrow diagrams with color-coded boxes. Uses `+`, `-`, `|` for structure.

```
+--------+   +-------+   /----------\
|        |   |       |   |          |
| Client +-->| Proxy +-->|{c} Cache |
|        |   |       |   |          |
+--------+   +-------+   \----------/
                 |
                 v
          /------+------\
          |{s}          |
          |  Database   |
          \-------------/
```

Color tags (placed inside box as first non-space text):
| Tag | Color |
|-----|-------|
| `{c}` | Cyan |
| `{g}` | Green |
| `{r}` | Red |
| `{o}` | Orange/yellow |
| `{p}` | Pink |
| `{s}` | Storage shape (rounded top) |
| `{d}` | Document shape |

Rounded corners: use `/` and `\` instead of `+` at corners.  
Dashed lines: use spaces in the line (`- - - -`).

## pikchr — `pikchr`

Best for: precise geometric technical drawings — flowcharts with exact sizing, circuit-style diagrams. SVG only.

```pikchr
A: box "Ingest" width 1in height 0.5in fill "#dbeafe"
arrow right 0.5in
B: box "Parse" same fill "#dbeafe"
arrow right 0.5in
C: box "Validate" same fill "#fef3c7"
arrow right 0.5in
D: box "Store" same fill "#dcfce7"

arrow from C.s down 0.5in then right until even with D.s then to D.s dashed
"error" at last arrow .w ljust
```

Decision flow:
```pikchr
Start: oval "Start" fill lightgreen
arrow down
P: box "Process Input" width 1.2in
arrow down
D: diamond "Valid?" width 1in height 0.6in
arrow down "yes" ljust
End: oval "Output" fill lightblue

arrow from D.e right 0.6in "no" above then down until even with P.e then to P.e
```

Key concepts:
- Shapes: `box`, `circle`, `ellipse`, `oval`, `diamond`, `cylinder`, `file`, `dot`
- Positioning: `right of X`, `above X`, `at X.e` (compass points: `.n` `.s` `.e` `.w` `.ne` etc.)
- Named shapes: `Name: box "label"` then reference by name
- `same` copies previous shape's dimensions
- `arrow from A.e to B.w` explicit connection

## Choosing

| Need | Tool |
|------|------|
| Rich ASCII art, flowcharts, sequence sketches | svgbob |
| Color-coded box diagrams with storage/document shapes | ditaa |
| Precise geometric layout, exact sizing | pikchr |
