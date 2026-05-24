---
name: symbolator
description: Symbolator diagram syntax — generate hardware component symbols from VHDL or Verilog/SystemVerilog module definitions. Produces clean port-labeled component diagrams for HDL documentation.
---

# Symbolator

Symbolator generates graphical symbols for VHDL and Verilog/SystemVerilog hardware components — the kind of box-with-ports diagrams used in hardware design documentation.

## Rendering

```
convert_diagram("symbolator", source, "svg")
convert_diagram("symbolator", source, "png")
```

No companion required.

## VHDL Input

Symbolator parses VHDL entity declarations:

```vhdl
library ieee;
use ieee.std_logic_1164.all;

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

## Verilog/SystemVerilog Input

```verilog
module fifo #(
  parameter DATA_WIDTH = 8,
  parameter DEPTH      = 16
) (
  input  wire                  clk,
  input  wire                  rst_n,
  input  wire                  wr_en,
  input  wire [DATA_WIDTH-1:0] wr_data,
  input  wire                  rd_en,
  output wire [DATA_WIDTH-1:0] rd_data,
  output wire                  full,
  output wire                  empty,
  output wire [$clog2(DEPTH):0] count
);
```

## What Symbolator Generates

Symbolator produces a component symbol with:
- **Left side**: input ports
- **Right side**: output ports
- **Top**: generic/parameter values
- **Component name** in the center

Ports are color-coded by direction:
- Inputs: connected on the left
- Outputs: connected on the right
- Inouts (bidirectional): marked with double arrow

## Supported Port Types

Symbolator recognizes and labels these HDL types:
- `std_logic`, `std_ulogic`
- `std_logic_vector(N downto 0)`
- `unsigned`, `signed`
- `integer`, `natural`, `positive`
- `bit`, `bit_vector`
- Verilog: `wire`, `reg`, `logic`, `input`, `output`, `inout`
- Parametric widths: `[WIDTH-1:0]`

## Example: AXI-Lite Slave

```vhdl
entity axi_lite_slave is
  generic (
    C_S_AXI_DATA_WIDTH : integer := 32;
    C_S_AXI_ADDR_WIDTH : integer := 4
  );
  port (
    S_AXI_ACLK    : in  std_logic;
    S_AXI_ARESETN : in  std_logic;
    S_AXI_AWADDR  : in  std_logic_vector(C_S_AXI_ADDR_WIDTH-1 downto 0);
    S_AXI_AWVALID : in  std_logic;
    S_AXI_AWREADY : out std_logic;
    S_AXI_WDATA   : in  std_logic_vector(C_S_AXI_DATA_WIDTH-1 downto 0);
    S_AXI_WSTRB   : in  std_logic_vector((C_S_AXI_DATA_WIDTH/8)-1 downto 0);
    S_AXI_WVALID  : in  std_logic;
    S_AXI_WREADY  : out std_logic;
    S_AXI_BRESP   : out std_logic_vector(1 downto 0);
    S_AXI_BVALID  : out std_logic;
    S_AXI_BREADY  : in  std_logic;
    S_AXI_ARADDR  : in  std_logic_vector(C_S_AXI_ADDR_WIDTH-1 downto 0);
    S_AXI_ARVALID : in  std_logic;
    S_AXI_ARREADY : out std_logic;
    S_AXI_RDATA   : out std_logic_vector(C_S_AXI_DATA_WIDTH-1 downto 0);
    S_AXI_RRESP   : out std_logic_vector(1 downto 0);
    S_AXI_RVALID  : out std_logic;
    S_AXI_RREADY  : in  std_logic
  );
end entity axi_lite_slave;
```

## Tips

- Symbolator only renders the **first** entity/module found in the source
- Port order in the source determines port order in the symbol (top to bottom)
- Generic/parameter values appear in the component header
- Comments in the HDL are ignored — they don't appear in the symbol
- Primarily useful for hardware documentation; not a simulation tool

## See Also
- `wavedrom` skill — digital timing/waveform diagrams
- `bytefield` skill — protocol and bit-field layout diagrams
