---
name: seqdiag
description: seqdiag diagram syntax — sequence diagrams from the blockdiag family. Simple, clean notation for message flows between actors. Requires companion container.
---

# seqdiag

seqdiag generates sequence diagrams with clean, minimal syntax. Part of the blockdiag family.

## Rendering

```
convert_diagram("seqdiag", source, "svg")
convert_diagram("seqdiag", source, "png")
```

**Requires companion container.**

## Basic Syntax

```
seqdiag {
  Client -> Server -> Database;
  Client <- Server <- Database;
}
```

Left-to-right = forward message. Right-to-left = response. Arrows chain automatically from the connection order.

## Explicit Messages

```
seqdiag {
  Client -> Server [label = "POST /login"];
  Server -> DB     [label = "SELECT user"];
  Server <- DB     [label = "user row"];
  Client <- Server [label = "200 { token }"];
}
```

## Participant Labels

```
seqdiag {
  Client  [label = "Browser"];
  Server  [label = "FastAPI"];
  DB      [label = "PostgreSQL"];

  Client -> Server [label = "request"];
  Server -> DB     [label = "query"];
  Server <- DB     [label = "result"];
  Client <- Server [label = "response"];
}
```

## Self-Messages

```
seqdiag {
  A -> A [label = "validate()"];
}
```

## Activation Bars

```
seqdiag {
  activation = none;   // disable activation bars (enabled by default)

  A -> B [label = "call"];
  A <- B [label = "return"];
}
```

## Separators and Notes

```
seqdiag {
  A -> B [label = "step 1"];

  === Separator Label ===

  B -> C [label = "step 2"];

  ... note text ...

  C -> A [label = "step 3"];
}
```

`=== text ===` adds a horizontal separator line.
`... text ...` adds a note bar.

## Loops and Conditions

```
seqdiag {
  loop {
    A -> B [label = "poll"];
    A <- B [label = "pending"];
  }

  A -> B [label = "final request"];
  A <- B [label = "result"];
}
```

```
seqdiag {
  A -> B [label = "authenticate"];

  if {
    A <- B [label = "200 OK"];
  } else {
    A <- B [label = "401 Unauthorized"];
  }
}
```

## Edge Styles

```
seqdiag {
  A -> B  [label = "sync call"];
  A ->> B [label = "async call"];
  A <- B  [label = "sync return"];
  A <<- B [label = "async return"];
  A -> B  [label = "lost message", style = "dashed"];
}
```

## Colors

```
seqdiag {
  A [color = "lightblue"];
  B [color = "#ffe"];

  A -> B [color = "red", label = "error path"];
}
```

## Complete Example: OAuth2 Flow

```
seqdiag {
  Browser [label = "Browser"];
  App     [label = "App Server"];
  AuthSrv [label = "Auth Server"];
  API     [label = "Resource API"];

  Browser -> App     [label = "GET /protected"];
  Browser <- App     [label = "302 → /authorize"];
  Browser -> AuthSrv [label = "GET /authorize?client_id=..."];
  Browser <- AuthSrv [label = "Login page"];
  Browser -> AuthSrv [label = "POST credentials"];
  Browser <- AuthSrv [label = "302 → /callback?code=..."];
  Browser -> App     [label = "GET /callback?code=..."];
  App     -> AuthSrv [label = "POST /token {code}"];
  App     <- AuthSrv [label = "{ access_token }"];
  App     -> API     [label = "GET /data (Bearer token)"];
  App     <- API     [label = "200 { data }"];
  Browser <- App     [label = "200 Protected page"];
}
```

## Tips

- `seqdiag` is simpler than PlantUML sequence diagrams — no `activate`/`deactivate`, fewer options
- Use `===` separators to break up long flows into logical phases
- Self-messages (`A -> A`) work but activation bars can look odd with them
- For richer sequence diagrams with activation boxes, nesting, and alt/loop blocks, use PlantUML

## See Also
- `plantuml` skill — richer sequence diagrams with activation, alt, loop, par
- `blockdiag` skill — block/component diagrams (same family)
- `mermaid-diagrams` skill — sequenceDiagram type
