---
name: sequence-diagrams
description: Create sequence diagrams showing message flows between components, services, or actors over time. Covers PlantUML, Mermaid, and seqdiag with Obsidian-kroki rendering.
---

# Sequence Diagrams

Show message flows between participants over time — API calls, auth flows, protocol exchanges.

**Recommended:** PlantUML for complex flows (activation boxes, alt/loop/par). Mermaid for simple flows in markdown. seqdiag for minimal diagrams.

## In Obsidian (obsidian-kroki)

Use a fenced code block with the diagram type as language identifier — renders inline automatically.

## PlantUML — `plantuml`

Best for: activation boxes, conditionals, parallel flows, grouping.

```plantuml
@startuml
participant Client
participant API
participant DB

Client -> API: POST /login
activate API
API -> DB: SELECT user WHERE email=?
DB --> API: user row
alt valid credentials
    API --> Client: 200 { token }
else invalid
    API --> Client: 401 Unauthorized
end
deactivate API
@enduml
```

Key syntax:
- `->` call, `-->` return, `->>` async
- `activate` / `deactivate` show lifeline boxes
- `alt`/`else`/`end` for conditionals, `loop`, `par` for parallel
- `autonumber` to number messages
- `note over A,B: text` for annotations
- `box "Label" #color ... end box` to group participants

## Mermaid — `mermaid`

Best for: simple flows, markdown documents, GitHub rendering. Native in Obsidian (no obsidian-kroki needed for mermaid).

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

    note over A,D: Encrypted channel
```

Arrow types: `->>` (solid open), `-->>` (dashed open), `->` (solid closed), `-->` (dashed closed), `-x` (lost)

Loops and conditions:
```mermaid
sequenceDiagram
    loop Retry up to 3×
        Client->>API: request
        API-->>Client: response
    end
    alt success
        API-->>Client: 200
    else failure
        API-->>Client: 500
    end
```

## seqdiag — `seqdiag` (companion required)

Best for: dead-simple sequence diagrams with minimal syntax.

```
seqdiag {
  Browser -> API [label = "POST /login"];
  API -> DB     [label = "SELECT user"];
  API <- DB     [label = "user row"];
  Browser <- API [label = "200 { token }"];
}
```

Separators: `=== Phase Label ===` · Notes: `... note text ...`

## Choosing

| Need | Tool |
|------|------|
| Activation boxes, alt/loop/par | PlantUML |
| Inline in GitHub/GitLab markdown | Mermaid |
| Minimal syntax, no extras | seqdiag |
