---
name: uml-diagrams
description: Create UML class, state, component, and deployment diagrams. Covers PlantUML (full UML), Mermaid class/state, and nomnoml for quick sketches. Obsidian-kroki rendering.
---

# UML Diagrams

Formal object-oriented diagrams — class hierarchies, state machines, component structure, deployment.

**Recommended:** PlantUML for full UML compliance. Mermaid for class/state diagrams in markdown. nomnoml for quick lightweight sketches.

## In Obsidian (obsidian-kroki)

Use a fenced code block with the type as language identifier — renders inline automatically.

## PlantUML — `plantuml`

### Class Diagrams

```plantuml
@startuml
interface Repository {
    +findById(id): Entity
    +save(entity): void
}

abstract class BaseService {
    #repo: Repository
    +getById(id): Entity
}

class UserService {
    +createUser(dto): User
    +authenticate(email, pass): Token
}

class User {
    +id: int
    +email: String
    -passwordHash: String
    +isActive(): bool
}

class Order {
    +id: int
    +total: decimal
    +status: OrderStatus
    +place(): void
}

UserService --|> BaseService
BaseService --> Repository
User "1" --> "0..*" Order : places
UserService ..> User : creates
@enduml
```

Relationships:
| Symbol | Meaning |
|--------|---------|
| `<\|--` | Inheritance |
| `..\|>` | Implementation |
| `*--` | Composition |
| `o--` | Aggregation |
| `-->` | Association |
| `..>` | Dependency |

### State Diagrams

```plantuml
@startuml
[*] --> Idle
Idle --> Loading : fetch()
Loading --> Success : 200 OK
Loading --> Error : timeout / error
Success --> Idle : reset()
Error --> Idle : retry()
Error --> [*] : abort

state Loading {
    [*] --> Requesting
    Requesting --> Parsing : response received
    Parsing --> [*]
}
@enduml
```

### Component Diagrams

```plantuml
@startuml
package "Frontend" {
    [React App] as UI
}
package "Backend" {
    [FastAPI] as API
    [Auth Service] as Auth
    database "PostgreSQL" as DB
}
cloud "External" {
    [Stripe]
}

UI --> API : HTTPS
API --> Auth : validates
API --> DB : SQL
API --> Stripe : payments
@enduml
```

### Deployment Diagrams

```plantuml
@startuml
node "Production" {
    node "ECS" {
        [API Container] as API
        [Worker Container] as Worker
    }
    database "RDS" {
        [PostgreSQL] as DB
    }
}
API --> DB
Worker --> DB
@enduml
```

## Mermaid — `mermaid`

### Class Diagram

```mermaid
classDiagram
    class User {
        +int id
        +string email
        -string password
        +login() bool
    }
    class Order {
        +int id
        +decimal total
        +place() void
    }
    User "1" --> "0..*" Order : places
    User ..|> Authenticatable
```

### State Diagram

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Loading : fetch()
    Loading --> Success : 200 OK
    Loading --> Error : error
    Success --> Idle : reset()
    Error --> Idle : retry()

    state Loading {
        [*] --> Requesting
        Requesting --> Parsing
        Parsing --> [*]
    }
```

## nomnoml — `nomnoml`

Best for: quick UML-like sketches with minimal syntax, no XML.

```
#direction: right

[<interface> Repository|
  findById(id)
  save(entity)
]

[UserService|
  -repo: Repository
  |
  createUser()
  authenticate()
]

[<frame> Domain|
  [User|
    -id: int
    -email: string
    |
    +isActive()
  ]
  [Order|
    -id: int
    -total: decimal
    |
    +place()
  ]
  [User] -> [Order]
]

[UserService] -> [Repository]
[UserService] -:> [Repository]
```

Shapes: `[Name]` box · `[<abstract>]` · `[<interface>]` · `[<frame>]` group · `[<database>]` · `[<actor>]`  
Relations: `->` association · `-->` dependency · `-:>` realization · `->*` composition · `->+` aggregation

## Choosing

| Need | Tool |
|------|------|
| Full UML spec (class, state, component, deployment) | PlantUML |
| Class or state diagrams in markdown / GitHub | Mermaid |
| Quick sketch, minimal syntax | nomnoml |
