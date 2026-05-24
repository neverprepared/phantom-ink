---
name: pikchr
description: Pikchr diagram syntax — PIC-like language for technical line drawings, circuit-style diagrams, and precise geometric illustrations embedded in documentation.
---

# Pikchr

Pikchr is a PIC-like markup language for creating technical drawings with precise geometry. Designed to be embedded in documentation (SQLite docs use it extensively).

## Rendering

```
convert_diagram("pikchr", source, "svg")
```

SVG only. No companion required.

## Basic Shapes

```pikchr
box "Rectangle"
circle "Circle"
ellipse "Ellipse"
diamond "Diamond"
cylinder "Cylinder"
file "File"
dot
```

Each shape is placed relative to the previous one by default.

## Positioning

```pikchr
box "A"
box "B" at 2in,0        // absolute position
box "C" right of B      // relative to named object
box "D" above B         // above
arrow from A to C       // connect
```

Direction keywords: `right`, `left`, `up`, `down`, `above`, `below`, `same as`

## Lines and Arrows

```pikchr
arrow right 2in
line right 1in
spline from A to B to C    // curved through points
arc from A to B            // arc
```

Arrow heads: `->` (forward), `<-` (back), `<->` (both), `-` (none)

```pikchr
line -> from A.e to B.w          // east edge to west edge
arrow from A.s to B.n dashed     // dashed arrow
line dotted from A to B          // dotted
```

## Size and Style

```pikchr
box width 2in height 0.5in "Label"
circle radius 0.3in fill lightblue
box color red thickness 2px
arrow color blue
```

## Compass Points (Anchors)

Every shape has anchors: `.n`, `.s`, `.e`, `.w`, `.ne`, `.nw`, `.se`, `.sw`, `.c` (center)

```pikchr
A: box "Source"
B: box "Target" right of A

arrow from A.e to B.w
```

Named objects (assign to variable with `Label:`) can be referenced by name.

## Loops and Repetition

```pikchr
for i = 1 to 4 {
    box width 0.5in height 0.3in at i*0.6in, 0
}
```

## Complete Example: Pipeline

```pikchr
scale = 0.75

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

## Example: Decision Flow

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

## Text Placement

```pikchr
box "Main Label" "second line"     // multi-line text
"standalone text" at 1in, 1in     // floating label
"left" ljust                       // left-justified
"right" rjust                      // right-justified
"above" above last box             // relative to shape
```

## Tips

- Pikchr is whitespace-tolerant but position-sensitive — test in a live renderer
- Use named labels (`Name:`) for any shape you'll reference later
- `.e`/`.w` anchors are the most common for left-to-right flow diagrams
- `same` copies the previous shape's dimensions: `box same at ...`
- No color names beyond basic CSS colors — use hex or named colors
- Primarily for technical documentation diagrams, not business diagrams

## See Also
- `svgbob` skill — ASCII art to SVG (simpler to write, less precise)
- `graphviz-diagrams` skill — when graph layout is more important than geometry
- `ditaa` skill — box-and-arrow ASCII art
