---
name: rackdiag
description: rackdiag diagram syntax — server rack layout diagrams from the blockdiag family. Shows physical rack units, servers, switches, and patch panels in a rack enclosure. Requires companion container.
---

# rackdiag

rackdiag renders server rack diagrams — the physical layout of equipment in a rack enclosure, measured in rack units (U). Part of the blockdiag family.

## Rendering

```
convert_diagram("rackdiag", source, "svg")
convert_diagram("rackdiag", source, "png")
```

**Requires companion container.**

## Basic Syntax

```
rackdiag {
  rack {
    4U; "Server 1";
    4U; "Server 2";
    2U; "Storage Array";
    1U; "KVM Switch";
  }
}
```

Format: `<units>U; "Label";`

## Rack Configuration

```
rackdiag {
  rack {
    description = "Production Rack A";
    units = 42;         // total rack height in U (default 10)
    unit_height = 15;   // pixels per U

    // Equipment
    2U; "Patch Panel";
    1U; "24-Port Switch";
    1U; "1U Gap";       // empty space
    4U; "Web Server 1" [color = "lightblue"];
    4U; "Web Server 2" [color = "lightblue"];
    2U; "Firewall" [color = "pink"];
    8U; "Storage Array" [color = "lightyellow"];
  }
}
```

## Node Attributes

```
rackdiag {
  rack {
    4U; "Server" [color = "lightblue", label = "App Server\n10.0.0.10"];
    2U; "Switch" [color = "lightgreen", label = "Core Switch"];
  }
}
```

| Attribute | Values |
|-----------|--------|
| `color` | CSS color name or hex |
| `label` | Override display text |
| `description` | Hover/annotation text |

## Multiple Racks

```
rackdiag {
  rack {
    description = "Rack A — Web Tier";
    units = 20;

    1U; "Top of Rack Switch";
    4U; "Web 01" [color = "lightblue"];
    4U; "Web 02" [color = "lightblue"];
    4U; "Web 03" [color = "lightblue"];
  }

  rack {
    description = "Rack B — App Tier";
    units = 20;

    1U; "Top of Rack Switch";
    4U; "App 01" [color = "lightgreen"];
    4U; "App 02" [color = "lightgreen"];
    4U; "App 03" [color = "lightgreen"];
  }

  rack {
    description = "Rack C — Data Tier";
    units = 20;

    1U; "Top of Rack Switch";
    4U; "DB Primary" [color = "lightyellow"];
    4U; "DB Replica" [color = "lightyellow"];
    2U; "Backup Appliance";
    8U; "SAN Array";
  }
}
```

## Complete Example: Small Data Center

```
rackdiag {
  rack {
    description = "Network Rack";
    units = 16;

    1U; "ODF / Fiber Patch Panel";
    1U; "Copper Patch Panel";
    2U; "Core Switch" [color = "lightgreen"];
    1U; "Firewall" [color = "pink"];
    1U; "VPN Concentrator" [color = "pink"];
    1U; "Out-of-Band Management";
    2U; "UPS Battery Module";
  }

  rack {
    description = "Server Rack 1";
    units = 16;

    1U; "Top-of-Rack Switch" [color = "lightgreen"];
    4U; "Compute 01\n2× Xeon, 256GB" [color = "lightblue"];
    4U; "Compute 02\n2× Xeon, 256GB" [color = "lightblue"];
    4U; "Compute 03\n2× Xeon, 256GB" [color = "lightblue"];
    1U; "1U Gap";
    2U; "iDRAC/BMC Switch";
  }

  rack {
    description = "Storage Rack";
    units = 16;

    1U; "Top-of-Rack Switch" [color = "lightgreen"];
    2U; "NAS Head Node" [color = "lightyellow"];
    8U; "Disk Shelf (48× HDD)" [color = "lightyellow"];
    2U; "Backup Server";
    1U; "Tape Library Controller";
  }
}
```

## Tips

- `1U; "Empty";` or just `1U;` renders an empty slot — useful for gaps
- `units = 42` sets the rack height; equipment beyond this overflows
- `color` is the most effective way to distinguish equipment types visually
- Multiple racks render side-by-side
- rackdiag doesn't draw cable connections — it's for physical slot layout only

## See Also
- `nwdiag` skill — logical network topology diagrams
- `blockdiag` skill — component/block diagrams (same family)
