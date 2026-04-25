---
name: diagram-from-code
description: Generate architecture diagrams, dependency graphs, sequence diagrams, and ERDs from source code. Use when the user wants to visualize code structure, data flow, API interactions, database schemas, or module dependencies.
---

# Diagram from Code

You read source code and produce accurate diagrams. Given a codebase or module, generate Mermaid, Graphviz, PlantUML, or D2 diagrams that show real structure — not idealized architecture, but what the code actually does.

## Execution Model

1. Read the relevant source files
2. Extract: entities, relationships, data flow, dependencies
3. Choose the best diagram type for what's being shown
4. Generate the diagram source in the requested format (default: Mermaid)
5. Print rendering instructions

## Diagram Types

### Architecture / Component Diagram
**When**: Show how services, modules, or packages relate to each other
**Extract from**: import statements, API calls, message queues, shared databases

```mermaid
graph TB
    subgraph Frontend
        Web[Web App<br/>React]
        Mobile[Mobile App<br/>React Native]
    end
    subgraph Backend
        API[API Gateway<br/>Express]
        Auth[Auth Service<br/>Passport]
        Users[User Service<br/>FastAPI]
        Orders[Order Service<br/>Go]
    end
    subgraph Data
        PG[(PostgreSQL)]
        Redis[(Redis)]
        S3[(S3)]
    end
    Web --> API
    Mobile --> API
    API --> Auth
    API --> Users
    API --> Orders
    Auth --> Redis
    Users --> PG
    Orders --> PG
    Orders --> S3
```

### Sequence Diagram
**When**: Show request flow, API interactions, or multi-step processes
**Extract from**: HTTP handlers, service methods, middleware chains

```mermaid
sequenceDiagram
    participant C as Client
    participant G as API Gateway
    participant A as Auth Service
    participant U as User Service
    participant D as Database

    C->>G: POST /api/users
    G->>A: Validate JWT
    A-->>G: Valid (user_id: 123)
    G->>U: CreateUser(data)
    U->>D: INSERT INTO users
    D-->>U: user record
    U-->>G: 201 Created
    G-->>C: 201 { id, name, email }
```

### Entity Relationship Diagram
**When**: Show database schema, ORM models, or data relationships
**Extract from**: SQL migrations, ORM model definitions, schema files

```mermaid
erDiagram
    USERS ||--o{ ORDERS : places
    USERS {
        uuid id PK
        string email UK
        string name
        enum role
        timestamp created_at
    }
    ORDERS ||--|{ ORDER_ITEMS : contains
    ORDERS {
        uuid id PK
        uuid user_id FK
        enum status
        decimal total
        timestamp created_at
    }
    PRODUCTS ||--o{ ORDER_ITEMS : "included in"
    PRODUCTS {
        uuid id PK
        string name
        decimal price
        int stock
    }
    ORDER_ITEMS {
        uuid id PK
        uuid order_id FK
        uuid product_id FK
        int quantity
        decimal unit_price
    }
```

### Class / Module Diagram
**When**: Show type hierarchy, interfaces, and dependencies
**Extract from**: class definitions, interface declarations, type annotations

```mermaid
classDiagram
    class Repository {
        <<interface>>
        +GetByID(id string) (Entity, error)
        +List(opts ListOpts) ([]Entity, error)
        +Create(e Entity) error
        +Update(e Entity) error
        +Delete(id string) error
    }
    class UserRepository {
        -db *sql.DB
        +GetByID(id string) (User, error)
        +List(opts ListOpts) ([]User, error)
        +Create(u User) error
        +Update(u User) error
        +Delete(id string) error
    }
    class UserService {
        -repo UserRepository
        -cache Cache
        +GetUser(id string) (User, error)
        +CreateUser(req CreateUserReq) (User, error)
    }
    Repository <|.. UserRepository
    UserService --> UserRepository
    UserService --> Cache
```

### State Diagram
**When**: Show lifecycle of an entity with status transitions
**Extract from**: state machines, enum definitions, business logic

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Pending : submit()
    Pending --> Approved : approve()
    Pending --> Rejected : reject()
    Rejected --> Draft : revise()
    Approved --> Active : activate()
    Active --> Suspended : suspend()
    Suspended --> Active : resume()
    Active --> Closed : close()
    Closed --> [*]
```

### Dependency Graph
**When**: Show package/module dependencies, import trees
**Extract from**: go.mod, package.json, import statements

```mermaid
graph LR
    main --> cmd
    main --> internal/server
    cmd --> internal/config
    cmd --> internal/server
    internal/server --> internal/handlers
    internal/server --> internal/middleware
    internal/handlers --> internal/services
    internal/handlers --> internal/models
    internal/services --> internal/repository
    internal/repository --> internal/models
    internal/middleware --> internal/auth
```

### Flowchart
**When**: Show algorithm logic, decision trees, process flows
**Extract from**: function implementations, if/switch blocks, loops

```mermaid
flowchart TD
    A[Receive Request] --> B{Authenticated?}
    B -->|No| C[Return 401]
    B -->|Yes| D{Rate Limited?}
    D -->|Yes| E[Return 429]
    D -->|No| F{Valid Input?}
    F -->|No| G[Return 400]
    F -->|Yes| H[Process Request]
    H --> I{Success?}
    I -->|Yes| J[Return 200]
    I -->|No| K[Return 500]
```

## Graphviz (for Complex Graphs)

Use Graphviz when Mermaid can't handle:
- Large graphs (50+ nodes)
- Custom layout algorithms (dot, neato, fdp, sfdp)
- Fine-grained positioning
- Subgraph clustering with complex edges

```dot
digraph architecture {
    rankdir=LR;
    node [shape=box, style=filled, fillcolor="#e8f4f8"];
    
    subgraph cluster_frontend {
        label="Frontend";
        style=dashed;
        web [label="Web App"];
        mobile [label="Mobile App"];
    }
    
    subgraph cluster_backend {
        label="Backend";
        style=dashed;
        api [label="API Gateway"];
        auth [label="Auth"];
        users [label="Users"];
    }
    
    web -> api;
    mobile -> api;
    api -> auth;
    api -> users;
}
```

## D2 (for Polished Diagrams)

```d2
direction: right

frontend: Frontend {
  web: Web App
  mobile: Mobile App
}

backend: Backend {
  api: API Gateway
  auth: Auth Service
  users: User Service
}

data: Data Layer {
  pg: PostgreSQL {shape: cylinder}
  redis: Redis {shape: cylinder}
}

frontend.web -> backend.api
frontend.mobile -> backend.api
backend.api -> backend.auth
backend.api -> backend.users
backend.users -> data.pg
backend.auth -> data.redis
```

## Rules

- **Accuracy over aesthetics** — diagram must match the actual code, not an idealized version
- **Label edges** — show what flows between nodes (HTTP, gRPC, events, data)
- **Show direction** — use arrows to indicate data/control flow direction
- **Group related nodes** — use subgraphs/clusters for logical grouping
- **Include types** — show key types, not just names (e.g., `PostgreSQL` not just `DB`)
- **Reasonable scope** — don't diagram the entire codebase; focus on what the user asked about
- **No orphan nodes** — every node should have at least one connection

## Rendering Instructions

```bash
# Mermaid → SVG (via CLI)
npx -y @mermaid-js/mermaid-cli mmdc -i diagram.mmd -o diagram.svg

# Graphviz → SVG
dot -Tsvg diagram.dot -o diagram.svg

# D2 → SVG
d2 diagram.d2 diagram.svg

# PlantUML → SVG
plantuml -tsvg diagram.puml
```

## Output Format

Write the diagram source to a file using apply_patch. Print the rendering command. If the user wants multiple diagram types for the same code, generate all in separate files.
