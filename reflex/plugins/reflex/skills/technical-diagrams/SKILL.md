---
name: technical-diagrams
description: Create hardware and protocol diagrams — WaveDrom (digital timing/waveforms), bytefield (packet/bit-field layouts), packetdiag (simpler packet structures), and symbolator (HDL component symbols).
---

# Technical Diagrams

Specialized diagrams for hardware design, protocol documentation, and signal analysis.

**Recommended:** wavedrom for digital timing diagrams. bytefield for protocol packet layouts (no companion needed). packetdiag for simpler packet diagrams. symbolator for HDL component symbols.

## In Obsidian (obsidian-kroki)

Use a fenced code block with the type as language identifier — renders inline automatically.

## WaveDrom — `wavedrom`

Best for: digital timing diagrams, signal waveforms, bus transactions. Source is JSON.

```json
{ "signal": [
  { "name": "CLK",  "wave": "p......." },
  { "name": "CS#",  "wave": "10......1" },
  { "name": "MOSI", "wave": "x.=.=.=.x", "data": ["CMD", "ADDR", "DATA"] },
  { "name": "MISO", "wave": "z.......=.", "data": ["ACK"] }
],
"config": { "hscale": 2 }}
```

Wave character encoding:
| Char | Meaning |
|------|---------|
| `p`/`n` | Clock (positive/negative edge) |
| `0`/`1` | Logic low/high |
| `=` | Data (bus), extends previous |
| `x` | Unknown/undefined |
| `z` | High impedance |
| `.` | Extend previous state |
| `2`–`9` | Colored data segments |
| `\|` | Time gap (break) |

Groups and labels:
```json
{ "signal": [
  ["Input",
    { "name": "CLK",   "wave": "P......." },
    { "name": "RESET", "wave": "10......" }
  ],
  {},
  ["Output",
    { "name": "OUT",  "wave": "x.1.0..1" },
    { "name": "DONE", "wave": "0.....10" }
  ]
]}
```

Edge annotations:
```json
{
  "signal": [
    { "name": "CLK",  "wave": "p.....", "node": "..A..B" },
    { "name": "DATA", "wave": "x.1..x", "node": "...C.." }
  ],
  "edge": ["A->C setup time", "C->B hold time"]
}
```

## bytefield — `bytefield`

Best for: RFC-style protocol packet layouts with variable-length fields. Uses a Clojure-like DSL. No companion required.

```
(defattrs :bg-blue   {:fill "#dbeafe"})
(defattrs :bg-yellow {:fill "#fef3c7"})
(defattrs :bg-green  {:fill "#dcfce7"})

(draw-column-headers)

(draw-box "Version"    {:span 4  :fill "#dbeafe"})
(draw-box "IHL"        {:span 4  :fill "#dbeafe"})
(draw-box "DSCP"       {:span 6  :fill "#fef3c7"})
(draw-box "ECN"        {:span 2  :fill "#fef3c7"})
(draw-box "Total Length" {:span 16 :fill "#dcfce7"})

(draw-box "Identification" {:span 16})
(draw-box "Flags"          {:span 3})
(draw-box "Fragment Offset" {:span 13})

(draw-box "TTL"            {:span 8})
(draw-box "Protocol"       {:span 8})
(draw-box "Header Checksum" {:span 16})

(draw-box "Source IP Address"      {:span 32})
(draw-box "Destination IP Address" {:span 32})

(draw-gap "Options (variable)")
(draw-gap "Payload")
```

Key functions:
- `(draw-column-headers)` — bit position header (0–31)
- `(draw-box "Label" {:span N :fill "#hex"})` — field spanning N bits
- `(draw-row-header "Word 0")` — row label on left
- `(draw-gap "Label")` — variable-length field (wavy edges)
- `(draw-related-boxes ["A" "B" "C"])` — row of equal-width boxes
- Default is 32 bits per row; change with `(def boxes-per-row 8)`

## packetdiag — `packetdiag` (companion required)

Best for: simple packet field diagrams with less syntax than bytefield.

```
packetdiag {
  colwidth = 32;

  0-15:  Source Port     [color = "lightblue"];
  16-31: Destination Port [color = "lightblue"];
  32-63: Sequence Number  [color = "lightyellow"];
  64-95: Acknowledgment   [color = "lightyellow"];
  96-99: Data Offset;
  100-111: Reserved + Flags [color = "pink"];
  112-127: Window Size;
  128-143: Checksum;
  144-159: Urgent Pointer;
}
```

Format: `start_bit - end_bit: Label [color = "..."];`  
Config: `colwidth = 32` (bits per row) · `node_height = 72` (pixels per row)

## symbolator — `symbolator`

Best for: generating hardware component symbols from VHDL entity or Verilog module definitions. Inputs on left, outputs on right.

VHDL:
```vhdl
entity uart_rx is
  generic (
    BAUD_RATE : integer := 9600;
    CLK_FREQ  : integer := 50000000
  );
  port (
    clk      : in  std_logic;
    rst_n    : in  std_logic;
    rx       : in  std_logic;
    data_out : out std_logic_vector(7 downto 0);
    data_rdy : out std_logic;
    err      : out std_logic
  );
end entity uart_rx;
```

Verilog:
```verilog
module fifo #(
  parameter DATA_WIDTH = 8,
  parameter DEPTH = 16
) (
  input  wire                  clk,
  input  wire                  rst_n,
  input  wire                  wr_en,
  input  wire [DATA_WIDTH-1:0] wr_data,
  output wire [DATA_WIDTH-1:0] rd_data,
  output wire                  full,
  output wire                  empty
);
```

## Choosing

| Need | Tool |
|------|------|
| Digital timing / signal waveforms | wavedrom |
| Protocol packet layout (RFC-style, variable fields) | bytefield |
| Simple packet field diagram | packetdiag |
| HDL component symbol from VHDL/Verilog | symbolator |
