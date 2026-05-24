---
name: actdiag
description: actdiag diagram syntax — activity flow diagrams from the blockdiag family. Shows process steps, decision branches, and parallel lanes. Requires companion container.
---

# actdiag

actdiag generates activity diagrams — workflow charts with lanes showing which actor performs each step. Part of the blockdiag family.

## Rendering

```
convert_diagram("actdiag", source, "svg")
convert_diagram("actdiag", source, "png")
```

**Requires companion container.**

## Basic Flow

```
actdiag {
  start -> validate -> process -> notify -> end;
}
```

## Node Labels and Shapes

```
actdiag {
  start    [label = "Start",    shape = "beginpoint"];
  validate [label = "Validate Input"];
  decide   [label = "Valid?",   shape = "diamond"];
  process  [label = "Process"];
  error    [label = "Return Error"];
  end      [label = "End",      shape = "endpoint"];

  start -> validate -> decide;
  decide -> process  [label = "yes"];
  decide -> error    [label = "no"];
  process -> end;
  error -> end;
}
```

Shapes: `box` (default), `roundedBox`, `diamond`, `ellipse`, `note`, `beginpoint`, `endpoint`

## Swimlanes

```
actdiag {
  lane Customer {
    label = "Customer";
    submit [label = "Submit Order"];
    review [label = "Review Confirmation"];
  }

  lane System {
    label = "System";
    validate [label = "Validate Order"];
    charge   [label = "Process Payment"];
    confirm  [label = "Send Confirmation"];
  }

  lane Warehouse {
    label = "Warehouse";
    pick    [label = "Pick Items"];
    ship    [label = "Ship Order"];
  }

  submit -> validate -> charge -> confirm -> review;
  charge -> pick -> ship;
}
```

## Parallel Branches

```
actdiag {
  start   [shape = "beginpoint"];
  split   [shape = "box", label = "Parallel Start"];
  merge   [shape = "box", label = "Parallel End"];
  end     [shape = "endpoint"];

  start -> split;
  split -> taskA [label = "branch A"];
  split -> taskB [label = "branch B"];
  taskA -> merge;
  taskB -> merge;
  merge -> end;
}
```

## Edge Styles

```
actdiag {
  A -> B [label = "happy path"];
  A -> C [label = "error path", style = "dashed"];
  B -> D [color = "blue"];
}
```

## Colors

```
actdiag {
  A [color = "lightblue",   label = "User Action"];
  B [color = "lightgreen",  label = "System Process"];
  C [color = "lightyellow", label = "External Call"];

  A -> B -> C;
}
```

## Complete Example: Order Fulfillment

```
actdiag {
  orientation = portrait;

  lane Customer {
    placeOrder  [label = "Place Order"];
    reviewEmail [label = "Review Confirmation Email"];
  }

  lane OrderSystem {
    receiveOrder [label = "Receive Order"];
    validateData [label = "Validate Data",    shape = "diamond"];
    rejectOrder  [label = "Reject Order",     color = "pink"];
    createRecord [label = "Create Order Record"];
    chargeCard   [label = "Charge Payment",   shape = "diamond"];
    payFail      [label = "Payment Failed",   color = "pink"];
    sendConfirm  [label = "Send Confirmation Email"];
  }

  lane Warehouse {
    pickItems  [label = "Pick Items"];
    packOrder  [label = "Pack Order"];
    shipOrder  [label = "Ship Order"];
  }

  placeOrder  -> receiveOrder -> validateData;
  validateData -> createRecord [label = "valid"];
  validateData -> rejectOrder  [label = "invalid", style = "dashed"];
  createRecord -> chargeCard;
  chargeCard -> pickItems     [label = "success"];
  chargeCard -> payFail       [label = "failure", style = "dashed"];
  pickItems  -> packOrder -> shipOrder -> sendConfirm -> reviewEmail;
}
```

## Tips

- Swimlanes are defined with `lane LaneName { ... }` — nodes inside are assigned to that lane
- `orientation = portrait` for top-down flow; default is landscape (left-right)
- Diamond shapes for decisions don't auto-branch — you must add edges manually
- For complex decision trees, blockdiag may be clearer
- `beginpoint`/`endpoint` shapes render as filled circles (UML activity diagram convention)

## See Also
- `blockdiag` skill — component/block diagrams (same family)
- `seqdiag` skill — sequence diagrams (same family)
- `plantuml` skill — activity diagrams with richer branching and swimlane support
- `mermaid-diagrams` skill — flowchart type
