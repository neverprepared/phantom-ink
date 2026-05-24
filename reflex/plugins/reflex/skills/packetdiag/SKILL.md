---
name: packetdiag
description: packetdiag diagram syntax — network packet and protocol frame structure diagrams from the blockdiag family. Simple notation for byte-level protocol documentation. Requires companion container.
---

# packetdiag

packetdiag renders packet/frame structure diagrams — the bit-field layouts used in network protocol documentation. Part of the blockdiag family.

## Rendering

```
convert_diagram("packetdiag", source, "svg")
convert_diagram("packetdiag", source, "png")
```

**Requires companion container.**

## Basic Syntax

```
packetdiag {
  0-7: Source Port;
  8-15: Destination Port;
  16-31: Sequence Number;
  32-63: Acknowledgment Number;
}
```

Field format: `start_bit - end_bit: Label;`

## Single Bit Fields

```
packetdiag {
  0: Version;
  1-3: IHL;
  4-11: TOS;
  12-31: Total Length;
}
```

## Field Width

Fields span from the start bit to the end bit. The diagram draws them proportionally.

```
packetdiag {
  0-7:   Source Port (8 bits);
  8-15:  Dest Port (8 bits);
  16-31: Length (16 bits);
  32-63: Checksum (32 bits);
}
```

## Colors

```
packetdiag {
  colwidth = 32;
  node_height = 72;

  0-7:   Source Port   [color = "lightblue"];
  8-15:  Dest Port     [color = "lightblue"];
  16-31: Sequence Num  [color = "lightyellow"];
  32-63: Ack Number    [color = "lightyellow"];
  64-67: Data Offset   [color = "lightgreen"];
  68-71: Reserved      [color = "#ddd"];
  72-79: Flags         [color = "pink"];
  80-95: Window Size;
  96-111: Checksum;
  112-127: Urgent Ptr;
}
```

## Configuration

```
packetdiag {
  colwidth = 32;      // bits per row (default 32)
  node_height = 72;   // height of each row in pixels

  0-15: Source;
  16-31: Destination;
  32-63: Data;
}
```

## Complete Example: TCP Header

```
packetdiag {
  colwidth = 32;
  node_height = 72;

  0-15:   Source Port      [color = "lightblue"];
  16-31:  Destination Port [color = "lightblue"];
  32-63:  Sequence Number  [color = "lightyellow"];
  64-95:  Acknowledgment Number [color = "lightyellow"];
  96-99:  Data Offset      [color = "lightgreen"];
  100-105: Reserved        [color = "#e0e0e0"];
  106:    URG              [color = "pink"];
  107:    ACK              [color = "pink"];
  108:    PSH              [color = "pink"];
  109:    RST              [color = "pink"];
  110:    SYN              [color = "pink"];
  111:    FIN              [color = "pink"];
  112-127: Window Size;
  128-143: Checksum;
  144-159: Urgent Pointer;
}
```

## Example: IPv4 Header

```
packetdiag {
  colwidth = 32;

  0-3:   Version;
  4-7:   IHL;
  8-13:  DSCP;
  14-15: ECN;
  16-31: Total Length;
  32-47: Identification;
  48-50: Flags;
  51-63: Fragment Offset;
  64-71: TTL;
  72-79: Protocol;
  80-95: Header Checksum;
  96-127: Source IP Address;
  128-159: Destination IP Address;
}
```

## packetdiag vs bytefield

| Feature | packetdiag | bytefield |
|---------|------------|-----------|
| Syntax | Simple bit ranges | Clojure DSL |
| Colors | Yes | Yes |
| Variable-length fields | No | Yes (draw-gap) |
| Companion required | Yes | No |
| Complexity | Low | Medium |
| Row labels | No | Yes (draw-row-header) |

**Use packetdiag when:** You want simple, quick protocol field diagrams without writing Clojure-like syntax.
**Use bytefield when:** You need variable-length fields, row labels, or more layout control.

## See Also
- `bytefield` skill — more expressive protocol diagrams, no companion required
- `wavedrom` skill — digital timing/signal diagrams
- `blockdiag` skill — block/component diagrams (same family)
