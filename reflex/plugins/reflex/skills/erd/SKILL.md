---
name: erd
description: erd diagram syntax — simple entity-relationship diagrams for database schemas. Clean, minimal notation for entities, attributes, and cardinality relationships.
---

# erd (Entity-Relationship Diagrams)

erd is a minimal DSL for drawing entity-relationship diagrams from database schemas. Simpler than UML, faster to write than Graphviz.

## Rendering

```
convert_diagram("erd", source, "svg")
convert_diagram("erd", source, "png")
```

No companion required.

## Basic Syntax

```
# Entities: [EntityName]
# Attributes: one per line (indent optional)
# Relationships: Entity1 rel Entity2 [label]

[Person]
*name
age
+email

[Address]
*street
city
country

Person 1--* Address
```

## Attribute Modifiers

| Prefix | Meaning |
|--------|---------|
| `*attr` | Primary key (bold) |
| `+attr` | Foreign key |
| `attr`  | Regular attribute |

## Cardinality

| Notation | Meaning |
|----------|---------|
| `1--1`   | One to one |
| `1--*`   | One to many |
| `*--1`   | Many to one |
| `*--*`   | Many to many |
| `?--1`   | Zero or one to one |
| `1--?`   | One to zero or one |
| `?--*`   | Zero or one to many |

## Relationship Labels

```
Person 1--* Order [places]
Order *--* Product [contains]
```

## Full Example: E-commerce Schema

```
[Customer]
*id
name
email
+address_id

[Address]
*id
street
city
state
zip
country

[Order]
*id
+customer_id
placed_at
status
total

[OrderItem]
*id
+order_id
+product_id
quantity
unit_price

[Product]
*id
name
description
price
stock

[Category]
*name
description

Customer 1--? Address [lives at]
Customer 1--* Order [places]
Order 1--* OrderItem [contains]
Product 1--* OrderItem [appears in]
Product *--* Category [belongs to]
```

## Tips

- Entity names are case-sensitive
- Attributes are display-only — no types, no constraints beyond the prefix modifiers
- Relationships must reference exact entity names
- No support for composite keys or multi-attribute annotations
- For richer schema diagrams with types and constraints, use `dbml`

## erd vs dbml

| Feature | erd | dbml |
|---------|-----|------|
| Syntax simplicity | Very simple | Moderate |
| Data types | No | Yes |
| Constraints (unique, not null) | No | Yes |
| Indexes | No | Yes |
| Table groups | No | Yes |
| Rendering quality | Good | Excellent |

**Use erd when:** You want a quick, readable schema overview with no implementation details.
**Use dbml when:** You need full schema fidelity with types and constraints.

## See Also
- `dbml` skill — database schema with types and constraints
- `plantuml` skill — ER diagrams via PlantUML class notation
- `mermaid-diagrams` skill — erDiagram type
