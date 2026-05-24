---
name: d2-diagrams
description: D2 diagram language syntax — modern architecture diagrams, SQL tables, and flows with clean readable syntax. Renders via phantom-diagrams MCP (type: d2, SVG only).
---

# D2 Diagrams

D2 is a modern diagram scripting language with clean, readable syntax. Strong for architecture docs and SQL schema. Output is SVG only.

## Rendering

```
convert_diagram("d2", source, "svg")
```

Supported formats: `svg` only. No companion container required.

## Basic Shapes and Connections

```d2
# Shapes
web server: Web Server
database: PostgreSQL {shape: cylinder}
cache: Redis {shape: hexagon}

# Connections
web server -> database: SQL queries
web server -> cache: GET/SET
cache -> database: cache miss
```

Arrow syntax:
- `A -> B` directed
- `A <- B` reverse direction
- `A <-> B` bidirectional
- `A -- B` undirected

Labels on connections:
```d2
A -> B: label text
A -> B: {label: "multiword label"}
```

## Containers (Grouping)

```d2
frontend: {
    react: React App
    nginx: Nginx
}

backend: {
    api: FastAPI
    auth: Auth Service
    db: PostgreSQL {shape: cylinder}
}

cloud: {
    cdn: CloudFront
}

frontend.react -> backend.api: HTTPS
backend.api -> backend.auth: validates
backend.api -> backend.db: queries
cdn -> frontend.nginx: serves
```

## Sequences

```d2
shape: sequence_diagram

client: Client
api: API
db: Database

client -> api: POST /login
api -> db: SELECT user
db -> api: user row
api -> client: 200 { token }
```

## SQL Tables

```d2
users: {
    shape: sql_table
    id: int {constraint: primary_key}
    email: varchar(255) {constraint: unique}
    name: varchar(100)
    created_at: timestamp
}

orders: {
    shape: sql_table
    id: int {constraint: primary_key}
    user_id: int {constraint: foreign_key}
    total: decimal(10,2)
    status: varchar(20)
}

users.id -> orders.user_id
```

## Classes

```d2
User: {
    shape: class
    +id: int
    +email: string
    -password: string
    +login(): bool
    +logout(): void
}

Order: {
    shape: class
    +id: int
    +total: decimal
    +place(): void
}

User -> Order: places {style.stroke-dash: 5}
```

## Styling

```d2
web server: {
    style: {
        fill: "#dbeafe"
        stroke: "#3b82f6"
        border-radius: 8
        font-size: 14
        bold: true
    }
}

web server -> database: {
    style: {
        stroke: "#ef4444"
        stroke-width: 2
        stroke-dash: 5
        animated: true
    }
}
```

## Grid Layout

```d2
grid-rows: 2
grid-columns: 3

a: Service A
b: Service B
c: Service C
d: Service D
e: Service E
f: Service F
```

## Icons and Images

```d2
server: {
    icon: https://icons.terrastruct.com/tech/022-server.svg
}
```

## Layers and Scenarios

```d2
layers: {
    dev: {
        db: SQLite {shape: cylinder}
    }
    prod: {
        db: PostgreSQL {shape: cylinder}
        replica: Replica {shape: cylinder}
        db -> replica: replication
    }
}
```

## D2 vs Alternatives

| D2 | PlantUML | Mermaid | Graphviz |
|----|----------|---------|---------|
| Clean, readable syntax | Verbose but complete | Markdown-native | Most layout control |
| SQL tables built-in | No SQL tables | ER diagrams | No SQL |
| SVG only | svg/png/jpeg | svg/png | svg/png/jpeg |
| No companion needed | No companion needed | Companion needed | No companion needed |
| Modern feel | Enterprise UML | GitHub/GitLab native | Complex graphs |

**Use D2 when:** Clean architecture docs, SQL schema with relationships, you want readable source that non-engineers can follow.

**Use PlantUML when:** Full UML compliance matters, C4 model, sequence diagrams with activation boxes.

**Use Mermaid when:** Inline GitHub/GitLab rendering, simple flowcharts.

**Use Graphviz when:** Complex multi-level dependency graphs, precise node placement.

## See Also
- `kroki-diagrams` skill — all types, routing, format table
- `plantuml` skill — full UML alternative
- `mermaid-diagrams` skill — markdown-native alternative
