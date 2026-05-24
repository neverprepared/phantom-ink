---
name: mermaid-diagrams
description: Mermaid diagram syntax — flowcharts, sequence, class, ER, state, gantt, git graphs, mindmaps, and timelines. Renders via phantom-diagrams MCP (type: mermaid, requires companion container).
---

# Mermaid Diagrams

Mermaid is markdown-native and renders in GitHub, GitLab, Notion, and VS Code. Text-first, easy to read and diff.

## Rendering

```
convert_diagram("mermaid", source, "svg")
```

Supported formats: `svg`, `png`. **Requires companion container** — Kroki must be running with the mermaid companion.

## Flowcharts

```mermaid
flowchart TD
    A[Start] --> B{Authenticated?}
    B -->|Yes| C[Process Request]
    B -->|No| D[Return 401]
    C --> E{Valid?}
    E -->|Yes| F[Execute]
    E -->|No| G[Return 400]
    F --> H[Return 200]
```

Direction: `TD` (top-down), `LR` (left-right), `BT` (bottom-top), `RL`

Node shapes:
| Syntax | Shape |
|--------|-------|
| `[text]` | Rectangle |
| `(text)` | Rounded |
| `{text}` | Diamond (decision) |
| `((text))` | Circle |
| `>text]` | Asymmetric |
| `[[text]]` | Subroutine |
| `[(text)]` | Cylinder (DB) |
| `/text/` | Parallelogram |

Subgraphs:
```mermaid
flowchart LR
    subgraph Frontend
        UI[React App]
    end
    subgraph Backend
        API[FastAPI]
        DB[(PostgreSQL)]
    end
    UI -->|HTTPS| API
    API -->|SQL| DB
```

## Sequence Diagrams

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant D as DB

    C->>A: POST /login
    activate A
    A->>D: SELECT user
    D-->>A: user row
    A-->>C: 200 { token }
    deactivate A

    note over A,D: All queries use prepared statements
```

Arrow types:
| Syntax | Style |
|--------|-------|
| `->>` | Solid, open arrowhead |
| `-->>` | Dashed, open arrowhead |
| `->` | Solid, closed |
| `-->` | Dashed, closed |
| `-x` | Solid, X end |
| `--x` | Dashed, X end |

Loops, conditions, and parallel:
```mermaid
sequenceDiagram
    loop Retry up to 3 times
        Client->>API: request
        API-->>Client: response
    end

    alt success
        API-->>Client: 200
    else failure
        API-->>Client: 500
    end

    par Parallel calls
        API->>ServiceA: call
    and
        API->>ServiceB: call
    end
```

## Class Diagrams

```mermaid
classDiagram
    class User {
        +int id
        +string email
        -string password
        +login() bool
        +logout() void
    }

    class Order {
        +int id
        +decimal total
        +place() void
    }

    User "1" --> "0..*" Order : places
    User ..|> Authenticatable
    Order *-- LineItem
```

Relationships:
| Syntax | Meaning |
|--------|---------|
| `<\|--` | Inheritance |
| `..\|>` | Implementation |
| `*--` | Composition |
| `o--` | Aggregation |
| `-->` | Association |
| `..>` | Dependency |

## Entity-Relationship Diagrams

```mermaid
erDiagram
    USER {
        int id PK
        string email
        string name
    }
    ORDER {
        int id PK
        int user_id FK
        decimal total
        date placed_at
    }
    LINE_ITEM {
        int id PK
        int order_id FK
        int product_id FK
        int quantity
    }

    USER ||--o{ ORDER : places
    ORDER ||--|{ LINE_ITEM : contains
```

Cardinality: `||` (one), `o|` (zero or one), `}|` (one or more), `}o` (zero or more)

## State Diagrams

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Loading : fetch()
    Loading --> Success : 200 OK
    Loading --> Error : timeout
    Success --> Idle : reset()
    Error --> Idle : retry()
    Error --> [*] : abort

    state Loading {
        [*] --> Requesting
        Requesting --> Parsing : response
        Parsing --> [*]
    }
```

## Gantt Charts

```mermaid
gantt
    title Project Timeline
    dateFormat YYYY-MM-DD
    section Design
        Research       :done,    des1, 2024-01-01, 2024-01-07
        Wireframes     :done,    des2, 2024-01-07, 5d
        Mockups        :active,  des3, 2024-01-12, 3d
    section Development
        API            :         dev1, after des3, 14d
        Frontend       :         dev2, after dev1, 10d
    section Testing
        QA             :crit,    qa1, after dev2, 5d
```

## Git Graphs

```mermaid
gitGraph
    commit id: "init"
    branch feature/auth
    checkout feature/auth
    commit id: "add login"
    commit id: "add JWT"
    checkout main
    merge feature/auth
    branch hotfix
    checkout hotfix
    commit id: "fix XSS"
    checkout main
    merge hotfix
```

## Mindmaps

```mermaid
mindmap
    root((System))
        Frontend
            React
            Tailwind
        Backend
            FastAPI
            PostgreSQL
        DevOps
            Docker
            GitHub Actions
```

## Timeline

```mermaid
timeline
    title System Architecture Evolution
    2022 : Monolith
         : Single DB
    2023 : Service split
         : Added caching
    2024 : Event-driven
         : Multi-region
```

## Pie Charts

```mermaid
pie title Error Distribution
    "4xx Client" : 42
    "5xx Server" : 18
    "Network" : 8
    "Timeout" : 32
```

## Styling

```mermaid
flowchart TD
    A[Node A]
    B[Node B]
    A --> B

    style A fill:#2563eb,stroke:#3b82f6,color:#fff
    style B fill:#059669,stroke:#10b981,color:#fff

    classDef warning fill:#d97706,stroke:#f59e0b,color:#fff
    class A warning
```

Themes (set in config block):
```mermaid
%%{init: {'theme': 'dark', 'themeVariables': {'primaryColor': '#2563eb'}}}%%
flowchart TD
    A --> B
```

Available themes: `default`, `dark`, `forest`, `neutral`, `base`

## When to Use Mermaid vs Alternatives

| Need | Use |
|------|-----|
| GitHub/GitLab inline rendering | Mermaid |
| Complex layout control | Graphviz |
| Full UML (sequence with activation) | PlantUML |
| Modern architecture docs | D2 |
| Database schema | DBML or ERD type |
| Renders without companion | PlantUML, D2, Graphviz |

## See Also
- `kroki-diagrams` skill — all types, routing, companion requirements
- `plantuml` skill — full UML alternative
- `image-to-diagram` skill — convert images to Mermaid/DOT
