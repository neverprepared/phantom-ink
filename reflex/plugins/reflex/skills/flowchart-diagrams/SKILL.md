---
name: flowchart-diagrams
description: Create flowcharts, process flows, decision trees, and activity diagrams. Covers Mermaid flowchart, PlantUML activity, and actdiag with Obsidian-kroki rendering.
---

# Flowchart Diagrams

Show process flows, decision branches, and step-by-step workflows.

**Recommended:** Mermaid for most cases — simple syntax, renders natively in Obsidian and GitHub. PlantUML activity when you need swimlanes or complex branching. actdiag for minimal block-style flows.

## In Obsidian (obsidian-kroki)

Use a fenced code block with the type as language identifier — renders inline automatically. Mermaid also renders via Obsidian's native renderer.

## Mermaid — `mermaid`

Best for: quick flowcharts, decision trees, GitHub/GitLab inline rendering.

```mermaid
flowchart TD
    A([Start]) --> B[Receive Request]
    B --> C{Authenticated?}
    C -->|Yes| D{Valid Input?}
    C -->|No| E[Return 401]
    D -->|Yes| F[Process]
    D -->|No| G[Return 400]
    F --> H[Return 200]
    E --> Z([End])
    G --> Z
    H --> Z
```

Direction: `TD` top-down · `LR` left-right · `BT` bottom-top · `RL`

Node shapes: `[rect]` · `(rounded)` · `{diamond}` · `((circle))` · `[(cylinder)]` · `/parallelogram/`

Subgraphs:
```mermaid
flowchart LR
    subgraph Frontend
        UI[React]
    end
    subgraph Backend
        API[FastAPI]
        DB[(PostgreSQL)]
    end
    UI -->|HTTPS| API
    API -->|SQL| DB
```

## PlantUML Activity — `plantuml`

Best for: swimlanes showing which actor performs each step, complex branching with fork/join.

```plantuml
@startuml
|Customer|
start
:Submit Order;

|System|
:Validate Order;
if (Valid?) then (yes)
    :Process Payment;
    if (Payment OK?) then (yes)
        |Warehouse|
        :Pick & Pack;
        :Ship Order;
        |Customer|
        :Receive Confirmation;
    else (no)
        |Customer|
        :Payment Failed Notice;
    endif
else (no)
    |Customer|
    :Validation Error;
endif
stop
@enduml
```

Key syntax:
- `:Step name;` for activities
- `if (condition?) then (yes) ... else (no) ... endif`
- `fork ... fork again ... end fork` for parallel
- `|LaneName|` to switch swimlane
- `start` / `stop` / `end`

## actdiag — `actdiag` (companion required)

Best for: simple activity flows with lanes, minimal syntax.

```
actdiag {
  orientation = portrait;

  lane Customer {
    submit [label = "Submit Order"];
    confirm [label = "View Confirmation"];
  }
  lane System {
    validate [label = "Validate"];
    charge   [label = "Charge Card"];
    notify   [label = "Send Email"];
  }
  lane Warehouse {
    pick [label = "Pick Items"];
    ship [label = "Ship"];
  }

  submit -> validate -> charge -> pick -> ship -> notify -> confirm;
}
```

Node shapes: `box` (default) · `roundedBox` · `diamond` · `beginpoint` · `endpoint`

## Choosing

| Need | Tool |
|------|------|
| Most flowcharts | Mermaid |
| Swimlanes, complex branching, fork/join | PlantUML activity |
| Minimal syntax, blockdiag-family style | actdiag |
