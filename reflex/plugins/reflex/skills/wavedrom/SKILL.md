---
name: wavedrom
description: WaveDrom diagram syntax — digital timing diagrams and waveform charts for hardware, protocol, and signal documentation. JSON-based with precise cycle-accurate rendering.
---

# WaveDrom

WaveDrom renders digital timing diagrams — the waveform charts used in hardware documentation, protocol specs, and embedded systems documentation.

## Rendering

```
convert_diagram("wavedrom", source, "svg")
convert_diagram("wavedrom", source, "png")
```

No companion required. Source is JSON.

## Basic Signal

```json
{ "signal": [
  { "name": "CLK",  "wave": "p......." },
  { "name": "DATA", "wave": "x.3456x." },
  { "name": "VALID","wave": "0.1....0" }
]}
```

## Wave Encoding

Each character in `"wave"` represents one clock cycle:

| Char | Meaning |
|------|---------|
| `p` | Positive clock (rising edge) |
| `n` | Negative clock (falling edge) |
| `P` | Clock with arrow at rising edge |
| `N` | Clock with arrow at falling edge |
| `0` | Low signal |
| `1` | High signal |
| `=` | Data (bus), extends previous |
| `x` | Unknown/undefined (X) |
| `z` | High impedance (Z) |
| `u` | Pull-up |
| `d` | Pull-down |
| `.` | Extend previous state |
| `2`-`9` | Colored data segments |
| `|` | Gap (break in time) |

## Data Labels

Use `"data"` array to label bus transitions:

```json
{ "signal": [
  { "name": "CMD",  "wave": "x.=.=.=.x", "data": ["READ", "WRITE", "NOP"] },
  { "name": "ADDR", "wave": "x.=.=.=.x", "data": ["0x00", "0x04", "----"] },
  { "name": "ACK",  "wave": "0...1.0..." }
]}
```

## Groups and Gaps

```json
{ "signal": [
  ["Input Signals",
    { "name": "CLK",   "wave": "P......." },
    { "name": "RESET", "wave": "10......" }
  ],
  {},
  ["Output Signals",
    { "name": "OUT",   "wave": "x.1.0..1" },
    { "name": "DONE",  "wave": "0.....10" }
  ]
]}
```

A string in the signals array creates a group label. `{}` inserts a blank row gap.

## Period and Phase

```json
{ "signal": [
  { "name": "CLK",  "wave": "p.......", "period": 2 },
  { "name": "FAST", "wave": "p.......", "period": 0.5 },
  { "name": "DATA", "wave": "x.1.0...", "phase": 0.25 }
]}
```

- `"period"`: clock period multiplier (2 = half-speed, 0.5 = double-speed)
- `"phase"`: shift signal left/right in fractional cycles

## Complete Example: SPI Transaction

```json
{ "signal": [
  { "name": "SCLK",  "wave": "0.p........0" },
  { "name": "CS#",   "wave": "10.........1" },
  { "name": "MOSI",  "wave": "x.=.=.=.=.x.", "data": ["CMD","A15","A14","D7"] },
  { "name": "MISO",  "wave": "z.........=.", "data": ["D7"] }
],
"config": { "hscale": 2 }
}
```

## Example: I2C Start/Stop Conditions

```json
{ "signal": [
  { "name": "SCL", "wave": "1.0101010101010.1." },
  { "name": "SDA", "wave": "10.=.=.=.=.=.0.1.", "data": ["A6","A5","A4","A3","A2","A1","A0"] }
],
"head": { "text": "I2C Address Phase" }
}
```

## Example: Memory Read Cycle

```json
{ "signal": [
  { "name": "CLK",   "wave": "p.....|..."  },
  { "name": "ADDR",  "wave": "x.=...|=.x", "data": ["0xA0", "0xB0"] },
  { "name": "RD#",   "wave": "1.0..1|0.1" },
  { "name": "DATA",  "wave": "z...=.|z.=.", "data": ["0xFF", "0x42"] },
  { "name": "VALID", "wave": "0...1.0...1." }
],
"edge": [
  "A+B delay", "C+D setup"
]}
```

## Configuration

```json
{
  "signal": [...],
  "config": {
    "hscale": 2,        // horizontal scale (1-4)
    "skin": "default"   // "default" or "narrow"
  },
  "head": {
    "text": "Title",
    "tick": 0,          // tick numbering start
    "tock": -1          // alternative tick label
  },
  "foot": {
    "text": "Footer note",
    "tock": 9
  }
}
```

## Edge Annotations

```json
{
  "signal": [
    { "name": "CLK",  "wave": "p.....", "node": "..A..B" },
    { "name": "DATA", "wave": "x.1..x", "node": "...C.." }
  ],
  "edge": [
    "A->C setup time",
    "C->B hold time"
  ]
}
```

Nodes are labeled with letters in the `"node"` string, then referenced in `"edge"` annotations.

## Tips

- Each `.` extends the previous state — use liberally for readability
- Group related signals with array brackets and a label string
- `hscale` 2 or 3 makes diagrams more readable for complex protocols
- Bus signals (`2`-`9`) use colors to distinguish data phases
- Edge annotations require `"node"` markers on the signals being connected
- Signal names can include spaces

## See Also
- `bytefield` skill — byte/bit field protocol packet layouts
- `packetdiag` skill — packet structure diagrams
