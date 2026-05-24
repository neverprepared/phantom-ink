---
name: dbml
description: DBML (Database Markup Language) syntax for database schema diagrams — tables, columns, types, constraints, indexes, relationships, and table groups. Clean SQL-like notation.
---

# DBML — Database Markup Language

DBML produces clean database schema diagrams with full type and constraint fidelity. Designed to be readable by engineers and non-engineers alike.

## Rendering

```
convert_diagram("dbml", source, "svg")
convert_diagram("dbml", source, "png")
```

No companion required.

## Basic Table

```dbml
Table users {
  id integer [primary key, increment]
  email varchar(255) [unique, not null]
  name varchar(100) [not null]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}
```

## Column Settings

```dbml
Table example {
  id integer [pk, increment]          // primary key, auto-increment
  code varchar(50) [unique]           // unique constraint
  name varchar [not null]             // not null
  status varchar [default: 'active']  // default value
  note text [note: 'Internal use']    // column note/comment
  ref_id integer [ref: > other.id]    // inline foreign key
}
```

Shorthand: `pk` = `primary key`, `increment` = auto-increment/serial

## Foreign Keys (Ref)

```dbml
// Inline (in column definition)
Table orders {
  id integer [pk]
  user_id integer [ref: > users.id]
}

// Standalone ref block
Ref: orders.user_id > users.id            // many-to-one
Ref: users.id < orders.user_id            // one-to-many
Ref: users.id - profiles.user_id          // one-to-one
Ref: orders.id <> tags.order_id           // many-to-many

// Named ref with delete/update actions
Ref fk_order_user: orders.user_id > users.id [delete: cascade, update: no action]
```

Cardinality:
- `>` many-to-one (this table's column → other's pk)
- `<` one-to-many
- `-` one-to-one
- `<>` many-to-many

## Indexes

```dbml
Table orders {
  id integer [pk]
  user_id integer
  status varchar
  created_at timestamp

  indexes {
    user_id                         // single column
    (user_id, status)               // composite
    created_at [name: 'idx_created']
    (user_id, status) [unique]
    status [type: hash]             // hash index
  }
}
```

## Enums

```dbml
Enum order_status {
  pending
  processing
  shipped
  delivered
  cancelled [note: 'Final state']
}

Table orders {
  id integer [pk]
  status order_status [not null, default: 'pending']
}
```

## Table Groups

```dbml
TableGroup auth {
  users
  sessions
  oauth_tokens
}

TableGroup commerce {
  orders
  order_items
  products
  categories
}
```

## Notes and Comments

```dbml
// Single-line comment

Table users {
  id integer [pk]
  Note: 'Core user table — do not archive'
}

// Column note
email varchar [note: 'Verified email only']
```

## Complete Example: E-commerce Schema

```dbml
Table users {
  id integer [pk, increment]
  email varchar(255) [unique, not null]
  name varchar(100) [not null]
  role varchar(20) [not null, default: 'customer']
  created_at timestamp [default: `now()`]

  indexes {
    email [name: 'idx_users_email']
  }

  Note: 'Platform user accounts'
}

Table addresses {
  id integer [pk, increment]
  user_id integer [not null, ref: > users.id]
  street varchar(255) [not null]
  city varchar(100) [not null]
  state varchar(50)
  zip varchar(20)
  country varchar(2) [not null, default: 'US']
  is_default boolean [default: false]
}

Enum order_status {
  pending
  confirmed
  shipped
  delivered
  cancelled
  refunded
}

Table orders {
  id integer [pk, increment]
  user_id integer [not null, ref: > users.id]
  shipping_address_id integer [ref: > addresses.id]
  status order_status [not null, default: 'pending']
  subtotal decimal(10,2) [not null]
  tax decimal(10,2) [not null]
  total decimal(10,2) [not null]
  placed_at timestamp [default: `now()`]
  shipped_at timestamp
  delivered_at timestamp

  indexes {
    user_id
    status
    (user_id, status) [name: 'idx_orders_user_status']
  }
}

Table products {
  id integer [pk, increment]
  sku varchar(50) [unique, not null]
  name varchar(255) [not null]
  description text
  price decimal(10,2) [not null]
  stock integer [not null, default: 0]
  category_id integer [ref: > categories.id]
  active boolean [not null, default: true]
}

Table categories {
  id integer [pk, increment]
  name varchar(100) [unique, not null]
  parent_id integer [ref: > categories.id]
}

Table order_items {
  id integer [pk, increment]
  order_id integer [not null, ref: > orders.id]
  product_id integer [not null, ref: > products.id]
  quantity integer [not null]
  unit_price decimal(10,2) [not null]

  indexes {
    (order_id, product_id) [unique]
  }
}

TableGroup core {
  users
  addresses
}

TableGroup commerce {
  orders
  order_items
  products
  categories
}
```

## Tips

- Use `[pk, increment]` for auto-increment primary keys
- Inline `ref:` is fine for simple FKs; use standalone `Ref:` for delete/update actions
- Enums must be defined before the tables that reference them
- `TableGroup` is purely visual — groups tables in the diagram
- No support for check constraints, triggers, or views (use comments to document)

## See Also
- `erd` skill — simpler ER diagrams without types/constraints
- `mermaid-diagrams` skill — `erDiagram` for inline schema in markdown
