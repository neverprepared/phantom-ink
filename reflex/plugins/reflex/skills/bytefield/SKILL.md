---
name: bytefield
description: bytefield diagram syntax — protocol and data structure diagrams showing byte/bit field layouts. Uses a Clojure-like DSL. Ideal for network protocol documentation and binary format specs.
---

# bytefield

bytefield renders protocol and binary format diagrams — the kind you see in RFC documents showing packet layouts, frame structures, and binary data formats.

## Rendering

```
convert_diagram("bytefield", source, "svg")
convert_diagram("bytefield", source, "png")
```

No companion required.

## Core Concepts

bytefield uses a Clojure-inspired DSL. Every diagram starts with a column header and draws boxes row by row.

## Basic Layout

```
(defattrs :bg-green {:fill "#a0ffa0"})
(defattrs :bg-yellow {:fill "#ffffb0"})
(defattrs :bg-pink {:fill "#ffb0a0"})

(draw-column-headers)

(draw-box "Version" {:span 1})
(draw-box "IHL" {:span 1})
(draw-box "DSCP" {:span 1})
(draw-box "ECN" {:span 1})
(draw-box "Total Length" {:span 4})
```

## Key Functions

### Headers and Labels

```
(draw-column-headers)           ; bit position header (0-31 by default)
(draw-column-headers {:labels ["0" "7" "8" "15" "16" "23" "24" "31"]})

(draw-row-header "Byte 0")      ; label on the left
(draw-row-header "Word 1" {:span 2})
```

### Boxes

```
(draw-box "Label")                          ; single-column box
(draw-box "Wide Field" {:span 4})           ; span multiple columns
(draw-box "Colored" :bg-green)              ; with background color
(draw-box "Styled" {:span 2 :fill "#ddf"}) ; span + color
(draw-box nil)                              ; empty/unnamed box
```

### Gaps and Related Boxes

```
(draw-gap "Variable Data")      ; wavy-edge box for variable-length field
(draw-related-boxes ["A" "B" "C" "D"])  ; row of equal boxes
```

### Borders

```
(draw-box "Field" {:borders #{}})           ; no border
(draw-box "Field" {:borders #{:top :bottom}})  ; top/bottom only
(draw-box "Field" {:borders #{:left :right}})  ; sides only
```

## Complete Example: IPv4 Header

```
(defattrs :bg-green {:fill "#a0ffa0"})
(defattrs :bg-yellow {:fill "#ffffb0"})
(defattrs :bg-pink {:fill "#ffb0b0"})

(draw-column-headers)

(draw-box "Version" {:span 4 :borders #{:left :top :bottom}})
(draw-box "IHL" {:span 4 :borders #{:top :bottom}})
(draw-box "DSCP" {:span 6 :borders #{:top :bottom}})
(draw-box "ECN" {:span 2 :borders #{:top :bottom}})
(draw-box "Total Length" {:span 16 :borders #{:top :right :bottom}})

(draw-box "Identification" {:span 16 :borders #{:left :top :bottom}})
(draw-box "Flags" {:span 3 :borders #{:top :bottom}})
(draw-box "Fragment Offset" {:span 13 :borders #{:top :right :bottom}})

(draw-box "TTL" {:span 8 :borders #{:left :top :bottom}})
(draw-box "Protocol" {:span 8 :borders #{:top :bottom}})
(draw-box "Header Checksum" {:span 16 :borders #{:top :right :bottom}})

(draw-box "Source IP Address" {:span 32})
(draw-box "Destination IP Address" {:span 32})

(draw-gap "Options (if IHL > 5)")
(draw-gap "Data Payload")
```

## Example: Custom Protocol Frame

```
(defattrs :bg-blue {:fill "#a0c0ff"})
(defattrs :bg-gray {:fill "#e0e0e0"})

(draw-column-headers {:labels (range 8)})

(draw-row-header "Header")
(draw-box "Magic" {:span 2 :fill "#a0c0ff"})
(draw-box "Ver" {:span 1})
(draw-box "Flags" {:span 1})
(draw-box "Msg Type" {:span 2})
(draw-box "Reserved" {:span 2 :fill "#e0e0e0"})

(draw-row-header "Lengths")
(draw-box "Total Length" {:span 4})
(draw-box "Header Length" {:span 4})

(draw-row-header "IDs")
(draw-box "Sequence Number" {:span 4})
(draw-box "Session ID" {:span 4})

(draw-row-header "Payload")
(draw-gap "Variable Length Payload")
```

## Configuration

```
(def options {:length 32})          ; columns per row (default 32)
(def options {:left-margin 3})      ; left margin width
(def options {:boxes-per-row 8})    ; alternative column count

; Set at top of file:
(def boxes-per-row 8)
```

## Tips

- `span` counts in bits/columns relative to the `boxes-per-row` setting
- Default is 32 columns (matching 32-bit word width in network protocols)
- Use `:fill` for colors, not named attrs (attrs are shortcuts)
- `draw-gap` is for variable-length fields — shows a wavy/broken border
- Rows automatically wrap — total spans should equal `boxes-per-row` per row
- The DSL is a subset of Clojure; standard Clojure `def` and basic forms work

## See Also
- `packetdiag` skill — simpler packet diagram syntax (blockdiag family, companion required)
- `wavedrom` skill — digital timing/signal diagrams
