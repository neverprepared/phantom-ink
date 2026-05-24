---
name: svgbob
description: svgbob diagram syntax — rich ASCII art converted to clean SVG. Supports more shapes and line styles than ditaa. Good for technical diagrams, flowcharts, and network topologies.
---

# svgbob

svgbob converts ASCII art into clean, crisp SVG. More expressive than ditaa with better line routing and shape support.

## Rendering

```
convert_diagram("svgbob", source, "svg")
convert_diagram("svgbob", source, "png")
```

No companion required.

## Basic Shapes

```
Rectangles:
+--------+
| Box    |
+--------+

Rounded:
.--------.
| Box    |
'--------'

Diamond:
   +
  / \
 /   \
+     +
 \   /
  \ /
   +
```

## Arrows and Lines

```
Horizontal:     -->    <--    <-->    ---
Vertical:       |
                v

Diagonal:
  \   /
   v ^

Double-headed:  <-->
                 ^
                 |
                 v
```

```
.----------.     .---------.
|          |     |         |
|  Source  +---->|  Sink   |
|          |     |         |
'----------'     '---------'
      |
      |
      v
.----------.
|  Logger  |
'----------'
```

## Text Labels on Lines

```
A           B
+           +
|           |
+---HTTP--->+

or inline:

[Client] ---GET /api---> [Server]
```

## Flowchart Pattern

```
   .-------.
   | Start |
   '---+---'
       |
       v
  .----+----.
  | Process |
  '----+----'
       |
      / \
     /   \
    + OK? +
     \   /
      \ /
    +--+--+
    |     |
    v     v
  .---.  .---.
  |Yes|  | No|
  '---'  '---'
```

## Network Diagram

```
         Internet
            |
     .------+------.
     |   Firewall  |
     '------+------'
            |
    .-------+-------.
    |               |
.---+---.       .---+---.
|  Web  |       |  Web  |
| Srv 1 |       | Srv 2 |
'---+---'       '---+---'
    |               |
    '------+--------'
           |
      .----+----.
      |   DB    |
      '----------'
```

## Sequence-style Diagram

```
Client          API           DB
  |              |             |
  +--request---->|             |
  |              +---query---->|
  |              |<--results---+
  |<--response---+             |
  |              |             |
```

## ASCII Symbols

svgbob recognizes and renders cleanly:
- `+` corner/junction
- `-` horizontal line
- `|` vertical line
- `/` `\` diagonal lines
- `>` `<` `^` `v` arrowheads
- `*` filled circle (dot)
- `o` open circle
- `.` and `'` for rounded corners

## Example: State Machine

```
           .---------.
    .------>|  Idle   |<-------.
    |       '---------'        |
    |            |             |
    |         fetch()          |
    |            |             |
    |            v             |
    |       .---------.        |
    |       | Loading |        |
    |       '-+-----+-'        |
    |         |     |          |
    |       200     error      |
    |         |     |          |
    |         v     v          |
    |     .------. .-------.   |
    |     |  OK  | | Error |   |
    |     '--+---' '---+---'   |
    |        |         |       |
    reset()  |       retry()   |
    '--------'         '-------'
```

## Tips

- Characters must be consistent — mixed spacing breaks alignment
- Use spaces to maintain grid alignment
- Arrows pointing into shapes work best at the edge midpoint
- No color support (unlike ditaa)
- Output is always black-and-white SVG

## See Also
- `ditaa` skill — simpler ASCII art with color tag support
- `graphviz-diagrams` skill — when layout precision matters more than ASCII editability
