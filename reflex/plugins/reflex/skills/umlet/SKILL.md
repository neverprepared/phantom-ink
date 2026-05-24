---
name: umlet
description: UMLet diagram syntax — quick UML sketches using a simple text-in-box format. Good for fast class diagrams, component diagrams, and sequence sketches without verbose XML or macros.
---

# UMLet

UMLet is a fast UML sketching tool where each element is a box containing text that defines its appearance. No XML, no macros — just text in boxes with connections between them.

## Rendering

```
convert_diagram("umlet", source, "svg")
convert_diagram("umlet", source, "png")
```

No companion required. Source is UMLet's `.uxf` XML format.

## Source Format

UMLet diagrams are XML (`.uxf` format) with `<element>` blocks:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<diagram program="UMLet" version="14.3">
  <zoom_level>10</zoom_level>

  <element>
    <type>com.umlet.element.Class</type>
    <coordinates><x>10</x><y>10</y><w>200</w><h>100</h></coordinates>
    <panel_attributes>
      MyClass
      --
      -id: int
      -name: String
      --
      +getName(): String
      +setName(name: String): void
    </panel_attributes>
  </element>

</diagram>
```

## Element Types

| Type | UMLet class |
|------|-------------|
| Class | `com.umlet.element.Class` |
| Interface | `com.umlet.element.Class` (add `«interface»` text) |
| Note | `com.umlet.element.Note` |
| Actor | `com.umlet.element.Actor` |
| UseCase | `com.umlet.element.UseCase` |
| Relation | `com.umlet.element.Relation` |
| Sequence lifeline | `com.umlet.element.SequenceDiagram` |

## Class Element

```xml
<element>
  <type>com.umlet.element.Class</type>
  <coordinates><x>100</x><y>50</y><w>200</w><h>120</h></coordinates>
  <panel_attributes>
    bg=lightblue
    User
    --
    -id: int
    -email: String
    +name: String
    --
    +login(): boolean
    +logout(): void
  </panel_attributes>
</element>
```

Text structure in `panel_attributes`:
- First line(s) before `--`: class name (add `«interface»` or `«abstract»` above)
- Between first and second `--`: attributes
- After second `--`: methods

## Relation / Connection

```xml
<element>
  <type>com.umlet.element.Relation</type>
  <coordinates><x>200</x><y>50</y><w>150</w><h>50</h></coordinates>
  <panel_attributes>
    lt=->
    m1=1
    m2=*
    Order
  </panel_attributes>
</element>
```

Line types (`lt=`):
| Value | Meaning |
|-------|---------|
| `->` | Association (arrow) |
| `<-` | Association (reverse) |
| `<->` | Bidirectional |
| `-` | Association (no arrow) |
| `.>` | Dependency (dashed arrow) |
| `<.` | Dependency reverse |
| `<\|--` | Inheritance |
| `<\|..` | Realization |
| `o->` | Aggregation |
| `+->` | Composition |

## Note Element

```xml
<element>
  <type>com.umlet.element.Note</type>
  <coordinates><x>400</x><y>50</y><w>180</w><h>60</h></coordinates>
  <panel_attributes>This is a note explaining something important</panel_attributes>
</element>
```

## Actor (Use Case)

```xml
<element>
  <type>com.umlet.element.Actor</type>
  <coordinates><x>50</x><y>200</y><w>80</w><h>80</h></coordinates>
  <panel_attributes>Customer</panel_attributes>
</element>
```

## Background Colors

Set in `panel_attributes` as `bg=color`:
```
bg=lightblue
bg=lightyellow
bg=lightgreen
bg=pink
bg=orange
bg=#aabbcc    (hex)
```

## Complete Example: Class Diagram

```xml
<?xml version="1.0" encoding="UTF-8"?>
<diagram program="UMLet" version="14.3">
  <zoom_level>10</zoom_level>

  <element>
    <type>com.umlet.element.Class</type>
    <coordinates><x>10</x><y>10</y><w>200</w><h>130</h></coordinates>
    <panel_attributes>
      bg=lightblue
      User
      --
      -id: int
      -email: String
      -passwordHash: String
      --
      +authenticate(): boolean
      +getProfile(): Profile
    </panel_attributes>
  </element>

  <element>
    <type>com.umlet.element.Class</type>
    <coordinates><x>280</x><y>10</y><w>200</w><h>110</h></coordinates>
    <panel_attributes>
      bg=lightblue
      Order
      --
      -id: int
      -total: double
      -status: String
      --
      +place(): void
      +cancel(): void
    </panel_attributes>
  </element>

  <element>
    <type>com.umlet.element.Class</type>
    <coordinates><x>280</x><y>200</y><w>200</w><h>90</h></coordinates>
    <panel_attributes>
      bg=lightyellow
      «interface»
      Repository
      --
      +findById(id: int): Object
      +save(entity: Object): void
    </panel_attributes>
  </element>

  <element>
    <type>com.umlet.element.Relation</type>
    <coordinates><x>200</x><y>50</y><w>90</w><h>50</h></coordinates>
    <panel_attributes>lt=->
m1=1
m2=*
places</panel_attributes>
  </element>

</diagram>
```

## Tips

- Coordinates `<x>`, `<y>`, `<w>`, `<h>` are in pixels — plan layout before writing
- Relations need coordinates that span between the elements they connect
- Use `«interface»` or `«abstract»` as the first line in class `panel_attributes`
- UMLet is best for quick sketches — use PlantUML for precise, specification-grade UML
- The `zoom_level` affects rendering scale; 10 is the default

## See Also
- `plantuml` skill — full UML specification-grade diagrams
- `nomnoml` skill — simpler UML-like notation without XML
