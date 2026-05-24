---
name: plantuml
description: PlantUML diagram syntax for UML diagrams — sequence, class, activity, component, state, deployment, and C4 architecture. Renders via phantom-diagrams MCP (type: plantuml or c4plantuml).
---

# PlantUML

PlantUML covers the full UML spectrum plus C4 architecture. All diagrams are wrapped in `@startuml` / `@enduml`.

## Rendering

```
convert_diagram("plantuml", source, "svg")   # standard UML
convert_diagram("c4plantuml", source, "svg") # C4 model diagrams
```

Supported formats: `svg`, `png`, `jpeg`. No companion container required.

## Sequence Diagrams

```plantuml
@startuml
participant Client
participant API
participant DB

Client -> API: POST /login
activate API
API -> DB: SELECT user WHERE email=?
DB --> API: user row
API --> Client: 200 { token }
deactivate API
@enduml
```

Key syntax:
- `->` synchronous call, `-->` return
- `->>`  async (open arrowhead)
- `activate` / `deactivate` show lifeline boxes
- `alt`/`else`/`end` for conditionals
- `loop N times` for loops
- `group Label` for grouping
- `note left/right of Participant: text`
- `autonumber` to number messages

```plantuml
@startuml
autonumber
Client -> API: request
alt success
    API --> Client: 200 OK
else failure
    API --> Client: 500 Error
end
@enduml
```

## Class Diagrams

```plantuml
@startuml
class User {
    +id: int
    +email: string
    -password: string
    +login(): bool
}

class Order {
    +id: int
    +total: decimal
    +place(): void
}

interface Repository {
    +findById(id): Entity
    +save(entity): void
}

User "1" --> "0..*" Order : places
User ..|> Repository
Order *-- LineItem : contains
@enduml
```

Relationships:
| Symbol | Meaning |
|--------|---------|
| `<|--` | Inheritance (extends) |
| `..|>` | Implementation (implements) |
| `*--`  | Composition |
| `o--`  | Aggregation |
| `-->`  | Association |
| `..>`  | Dependency |

## Activity / Flowchart

```plantuml
@startuml
start
:Receive Request;
if (Authenticated?) then (yes)
    :Process Request;
    if (Valid Input?) then (yes)
        :Execute;
        :Return 200;
    else (no)
        :Return 400;
    endif
else (no)
    :Return 401;
endif
stop
@enduml
```

New (beta) syntax with swimlanes:
```plantuml
@startuml
|User|
start
:Submit Form;
|System|
:Validate;
if (Valid?) then (yes)
    :Save to DB;
    |User|
    :Show Success;
else (no)
    |User|
    :Show Errors;
endif
stop
@enduml
```

## Component Diagrams

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
    [Stripe API]
}

UI --> API : HTTPS
API --> Auth : validates token
API --> DB : SQL
API --> [Stripe API] : payments
@enduml
```

## State Diagrams

```plantuml
@startuml
[*] --> Idle
Idle --> Loading : fetch()
Loading --> Success : 200 OK
Loading --> Error : timeout/error
Success --> Idle : reset()
Error --> Idle : retry()
Error --> [*] : give up
@enduml
```

## Deployment Diagrams

```plantuml
@startuml
node "Production" {
    node "Web Tier" {
        [Nginx] as LB
        [App Server 1] as APP1
        [App Server 2] as APP2
    }
    database "DB Cluster" {
        [Primary] as PRI
        [Replica] as REP
    }
}

LB --> APP1
LB --> APP2
APP1 --> PRI
APP2 --> PRI
PRI --> REP : replication
@enduml
```

## C4 Model (c4plantuml type)

```plantuml
@startuml
!include C4_Context.puml

Person(user, "User", "An end user")
System(system, "My System", "Does things")
System_Ext(ext, "External API", "Third party")

Rel(user, system, "Uses", "HTTPS")
Rel(system, ext, "Calls", "REST")
@enduml
```

Available includes:
- `C4_Context.puml` — System Context
- `C4_Container.puml` — Containers
- `C4_Component.puml` — Components
- `C4_Dynamic.puml` — Dynamic diagrams
- `C4_Deployment.puml` — Deployment

Use `convert_diagram("c4plantuml", source, "svg")` for C4 diagrams.

## Styling

```plantuml
@startuml
skinparam backgroundColor white
skinparam sequenceArrowThickness 2
skinparam roundcorner 5
skinparam componentStyle rectangle

skinparam component {
    BackgroundColor LightBlue
    BorderColor DarkBlue
    FontColor Black
}

!theme plain
@enduml
```

Common themes: `plain`, `amiga`, `aws-orange`, `blueprint`, `cerulean`, `hacker`, `sketchy`

## Useful Shortcuts

```plantuml
' Comments start with single quote
title My Diagram Title
header Left Header
footer Page %page% of %lastpage%

' Grouping in sequence
box "Internal Services" #LightBlue
    participant API
    participant DB
end box

' Notes
note over API,DB: Encrypted channel
note left: side note
```

## See Also
- `kroki-diagrams` skill — type routing, format table, other tools
- `graphviz-diagrams` skill — when you need precise layout control
