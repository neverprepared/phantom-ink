---
name: nwdiag
description: nwdiag diagram syntax — network topology diagrams from the blockdiag family. Shows servers, devices, and network segments with IP addresses and connections. Requires companion container.
---

# nwdiag

nwdiag generates network topology diagrams — showing servers and devices grouped into network segments. Part of the blockdiag family.

## Rendering

```
convert_diagram("nwdiag", source, "svg")
convert_diagram("nwdiag", source, "png")
```

**Requires companion container.**

## Basic Syntax

```
nwdiag {
  network internet {
    address = "0.0.0.0/0";
    web01 [address = "203.0.113.1"];
  }

  network dmz {
    address = "10.0.0.0/24";
    web01   [address = "10.0.0.10"];
    app01   [address = "10.0.0.20"];
  }

  network internal {
    address = "192.168.0.0/24";
    app01 [address = "192.168.0.20"];
    db01  [address = "192.168.0.30"];
  }
}
```

Nodes spanning multiple networks appear in each segment they're assigned to, connected by a bridge.

## Node Labels and Types

```
nwdiag {
  network lan {
    router   [label = "Router",    shape = "cisco.router"];
    switch   [label = "Switch",    shape = "cisco.layer_2_switch"];
    firewall [label = "Firewall",  shape = "cisco.firewall"];
    server01 [label = "Web Server"];
    laptop   [label = "Dev Laptop", shape = "workstation"];
  }
}
```

Shapes: `box` (default), `ellipse`, `roundedBox`, `diamond`, `note`, `cloud`, `mail`, `actor`, `beginpoint`, `endpoint`

Cisco-style shapes (if supported): `cisco.router`, `cisco.layer_2_switch`, `cisco.firewall`, `cisco.pc`

## Node Colors

```
nwdiag {
  network internal {
    webserver [color = "lightblue"];
    dbserver  [color = "lightyellow"];
    cache     [color = "lightgreen"];
  }
}
```

## Multiple Network Segments

```
nwdiag {
  network internet {
    address = "203.0.113.0/24";
    gateway;
  }

  network frontend {
    address = "10.1.0.0/24";
    gateway [address = "10.1.0.1"];
    lb01    [address = "10.1.0.10", label = "Load Balancer"];
    lb02    [address = "10.1.0.11", label = "Load Balancer"];
  }

  network appnet {
    address = "10.2.0.0/24";
    lb01    [address = "10.2.0.1"];
    lb02    [address = "10.2.0.2"];
    app01   [address = "10.2.0.10", label = "App Server 1"];
    app02   [address = "10.2.0.11", label = "App Server 2"];
    app03   [address = "10.2.0.12", label = "App Server 3"];
  }

  network dbnet {
    address = "10.3.0.0/24";
    app01 [address = "10.3.0.1"];
    app02 [address = "10.3.0.2"];
    app03 [address = "10.3.0.3"];
    db01  [address = "10.3.0.10", label = "Primary DB"];
    db02  [address = "10.3.0.11", label = "Replica DB"];
  }
}
```

## Complete Example: Three-Tier AWS Architecture

```
nwdiag {
  network internet {
    address = "0.0.0.0/0";
    cf [label = "CloudFront CDN", shape = "cloud"];
  }

  network public_subnet {
    address = "10.0.1.0/24";
    cf    [address = "10.0.1.1"];
    alb   [address = "10.0.1.10", label = "Application\nLoad Balancer"];
  }

  network app_subnet {
    address = "10.0.2.0/24";
    alb     [address = "10.0.2.1"];
    web01   [address = "10.0.2.10", label = "Web/App 1", color = "lightblue"];
    web02   [address = "10.0.2.11", label = "Web/App 2", color = "lightblue"];
    web03   [address = "10.0.2.12", label = "Web/App 3", color = "lightblue"];
  }

  network db_subnet {
    address = "10.0.3.0/24";
    web01   [address = "10.0.3.1"];
    web02   [address = "10.0.3.2"];
    web03   [address = "10.0.3.3"];
    primary [address = "10.0.3.10", label = "RDS Primary", color = "lightyellow"];
    replica [address = "10.0.3.11", label = "RDS Replica", color = "lightyellow"];
  }
}
```

## Tips

- Nodes listed in multiple networks automatically get multi-homed rendering (connected to each network bar)
- Network `address` is display-only — no routing logic
- Networks render as horizontal bars; nodes hang below them
- Node `address` labels appear under the node name in each network bar
- For logical architecture (not physical network topology), `blockdiag` is usually cleaner

## See Also
- `blockdiag` skill — component/block diagrams (same family)
- `rackdiag` skill — server rack layouts (same family)
- `graphviz-diagrams` skill — network graphs with more layout control
