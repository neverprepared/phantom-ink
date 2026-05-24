---
name: blockdiag
description: blockdiag diagram syntax — simple block/component diagrams with boxes and directed connections. Clean, minimal syntax for system architecture and component relationship diagrams. Requires companion container.
---

# blockdiag

blockdiag generates clean block diagrams from a simple text description. Part of the blockdiag family (seqdiag, actdiag, nwdiag share the same syntax base).

## Rendering

```
convert_diagram("blockdiag", source, "svg")
convert_diagram("blockdiag", source, "png")
```

**Requires companion container.**

## Basic Syntax

```
blockdiag {
  A -> B -> C;
  A -> D;
}
```

## Node Attributes

```
blockdiag {
  A [label = "Web Server"];
  B [label = "App Server", color = "lightblue"];
  C [label = "Database", shape = "box"];
  D [label = "Cache", color = "pink", style = "dashed"];

  A -> B -> C;
  A -> D;
}
```

Node attributes:
| Attribute | Values |
|-----------|--------|
| `label` | Display text |
| `color` | CSS color name or hex |
| `style` | `solid`, `dashed`, `dotted` |
| `shape` | `box` (default), `roundedBox`, `diamond`, `ellipse`, `note`, `mail`, `cloud`, `actor` |
| `fontsize` | Integer |
| `textcolor` | CSS color |

## Edge Attributes

```
blockdiag {
  A -> B [label = "HTTP"];
  B -> C [label = "SQL", style = "dashed"];
  C -> D [label = "replication", color = "red"];
}
```

## Groups

```
blockdiag {
  group frontend {
    label = "Frontend Tier";
    color = "#eef";
    Nginx; App1; App2;
  }

  group backend {
    label = "Backend Tier";
    color = "#efe";
    API; Worker;
  }

  group data {
    label = "Data Tier";
    color = "#fee";
    DB; Cache;
  }

  Nginx -> App1 -> API -> DB;
  Nginx -> App2 -> API;
  API -> Cache;
  API -> Worker;
}
```

## Orientation

```
blockdiag {
  orientation = portrait;   // top-to-bottom (default is landscape/left-to-right)

  A -> B -> C;
}
```

## Complete Example: Microservices

```
blockdiag {
  orientation = landscape;

  // Nodes
  Gateway [label = "API Gateway", color = "lightblue"];
  Auth    [label = "Auth Service"];
  Orders  [label = "Order Service"];
  Products[label = "Product Service"];
  Notify  [label = "Notification Svc"];
  OrderDB [label = "Orders DB", shape = "box", color = "lightyellow"];
  ProdDB  [label = "Products DB", shape = "box", color = "lightyellow"];
  Queue   [label = "Message Queue", shape = "roundedBox", color = "lightgreen"];

  // Groups
  group services {
    label = "Core Services";
    color = "#eef";
    Auth; Orders; Products;
  }

  group data {
    label = "Data Layer";
    color = "#fef";
    OrderDB; ProdDB; Queue;
  }

  // Connections
  Gateway -> Auth    [label = "verify"];
  Gateway -> Orders  [label = "POST /orders"];
  Gateway -> Products[label = "GET /products"];
  Orders  -> OrderDB [label = "SQL"];
  Orders  -> Queue   [label = "publish"];
  Products-> ProdDB  [label = "SQL"];
  Queue   -> Notify  [label = "consume"];
}
```

## Tips

- Node IDs are identifiers — use `label` for display text
- Duplicate edges are allowed (creates multiple arrows)
- Groups are visual only — they don't enforce routing
- All nodes in a group should be declared before the group references them
- `blockdiag` is best for simple left-to-right system overviews; use Graphviz for complex layouts

## See Also
- `seqdiag` skill — sequence diagrams (same family)
- `actdiag` skill — activity diagrams (same family)
- `nwdiag` skill — network diagrams (same family)
- `graphviz-diagrams` skill — more layout control
