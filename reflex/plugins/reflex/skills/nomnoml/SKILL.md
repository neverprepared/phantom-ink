---
name: nomnoml
description: nomnoml diagram syntax — lightweight UML-like diagrams with clean readable notation. Good for class relationships, component diagrams, and quick architecture sketches.
---

# nomnoml

nomnoml is a lightweight diagram tool with a minimal, readable syntax for UML-like diagrams. No XML, no verbose boilerplate.

## Rendering

```
convert_diagram("nomnoml", source, "svg")
convert_diagram("nomnoml", source, "png")
```

No companion required.

## Basic Shapes

```
[Simple box]

[<abstract> Abstract class]

[<interface> Interface]

[<note> This is a note]

[<database> Database]

[<start>]
[<end>]

[<actor> User]
```

Shape classifiers: `abstract`, `interface`, `note`, `database`, `start`, `end`, `actor`, `usecase`, `choice`, `frame`, `hidden`, `input`, `rhomb`, `round`, `receiver`, `sender`, `transceiver`

## Relationships

```
[A] -> [B]          association (directed)
[A] --> [B]         dependency (dashed)
[A] -:> [B]         realization/implementation
[A] -/-> [B]        inherited association
[A] ->+ [B]         aggregation
[A] ->* [B]         composition
[A] -- [B]          association (undirected)
```

## Labels

```
[A] -> [B] label on arrow
[A] label -> [B]    label near A
[A] -> label [B]    label near B
```

## Classifier with Compartments

```
[Customer|
  id: int
  name: string
  email: string
  |
  login()
  logout()
]
```

The `|` separates compartments: name | attributes | methods.

## Nesting / Grouping

```
[<frame> Backend |
  [API Service]
  [Auth Service]
  [Worker]
]
```

## Full Example: Application Architecture

```
#direction: right
#fontSize: 14
#lineWidth: 2

[<actor> User] -> [Web App]
[Web App] -> [<frame> Backend |
  [API Gateway] -> [Auth Service]
  [API Gateway] -> [Order Service]
  [API Gateway] -> [Product Service]
  [Order Service] ->+ [<database> Orders DB]
  [Product Service] ->+ [<database> Products DB]
]
[Web App] --> [<database> Session Store]
```

## Class Diagram Example

```
[<abstract> Repository|
  findById(id)
  save(entity)
  delete(id)
]

[UserRepository|
  findByEmail(email)
]

[OrderRepository|
  findByUser(userId)
  findByStatus(status)
]

[UserRepository] -:> [Repository]
[OrderRepository] -:> [Repository]

[UserService|
  createUser()
  authenticate()
] -> [UserRepository]

[OrderService|
  placeOrder()
  cancelOrder()
] -> [OrderRepository]
[OrderService] -> [UserService]
```

## Directives

```
#direction: down      (or right, left, up)
#fontSize: 12
#lineWidth: 1
#padding: 8
#spacing: 40
#background: transparent
#fill: #eef
#stroke: #33d
#arrowSize: 1
#bendSize: 0.3
#edges: hard           (or rounded)
#gutter: 5
#zoom: 1
#acyclicer: greedy
#ranker: network-simplex
```

## Tips

- `#direction: right` for left-to-right layout (default is top-down)
- Use compartments (`|`) to show attributes and methods in classes
- `<frame>` is great for grouping services or packages
- Colors can be set globally with `#fill` and `#stroke`, not per-node
- nomnoml is best for quick sketches — use PlantUML for full UML compliance

## See Also
- `plantuml` skill — full UML with more diagram types and styling
- `structurizr` skill — C4 model architecture diagrams
- `mermaid-diagrams` skill — class diagrams with GitHub-native rendering
