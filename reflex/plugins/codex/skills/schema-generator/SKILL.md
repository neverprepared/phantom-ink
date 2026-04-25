---
name: schema-generator
description: Generate schemas and type definitions from natural language descriptions. Use when the user needs OpenAPI specs, JSON Schema, Protobuf definitions, GraphQL SDL, database migrations, or Avro/Thrift schemas.
---

# Schema Generator

You produce valid, complete schemas from natural language descriptions. Output must parse without errors. No placeholders.

## Supported Formats

| Format | Output | Validator |
|--------|--------|-----------|
| OpenAPI | `openapi.yaml` (3.1) | `npx @redocly/cli lint` |
| JSON Schema | `.schema.json` (draft 2020-12) | `ajv validate` |
| Protobuf | `.proto` (proto3) | `protoc --lint_out=.` |
| GraphQL | `.graphql` (SDL) | `graphql-inspector validate` |
| SQL Migration | `.sql` (Postgres by default) | `psql -f` |
| Avro | `.avsc` | `avro-tools compile` |
| TypeScript | `.d.ts` or `types.ts` | `tsc --noEmit` |
| Pydantic | `models.py` | `python -c "import models"` |
| Zod | `schema.ts` | `tsc --noEmit` |

## Execution Model

1. Parse the natural language description into entities, relationships, and constraints
2. Infer field types from names and context (e.g., `email` → string + format:email, `created_at` → datetime)
3. Determine the output format from context or ask if ambiguous
4. Generate the complete schema in one pass
5. Print the validation command

## Rules

- **Every field gets a type** — never use `any`, `object`, or `string` as a catch-all
- **Every entity gets an ID** — `uuid` primary key unless specified otherwise
- **Timestamps** — always include `created_at` and `updated_at` unless explicitly excluded
- **Relationships** — use foreign keys (SQL), `$ref` (JSON Schema), nested messages (Protobuf), or connections (GraphQL)
- **Enums** — use enums for fields with a known finite set of values, never bare strings
- **Validation** — include constraints: required, min/max, pattern, unique where inferable
- **Naming** — snake_case for SQL/Python/Protobuf, camelCase for TypeScript/GraphQL, PascalCase for types

## Multi-Format Output

If the user describes a domain model without specifying a format, generate:
1. SQL migration (source of truth)
2. TypeScript types (frontend)
3. One of: Pydantic models, Go structs, or Zod schemas (backend, match project language)

## SQL Migration Specifics

- Target Postgres unless specified
- Use `CREATE TABLE` with explicit types, constraints, indexes
- Include `CREATE INDEX` for foreign keys and commonly queried fields
- Add `ON DELETE CASCADE` / `SET NULL` based on relationship semantics
- Generate both `up` and `down` migrations
- Use transactions: `BEGIN; ... COMMIT;`
- Add comments on non-obvious columns

## OpenAPI Specifics

- Version 3.1.0
- Include `info`, `servers`, `paths`, `components/schemas`
- Every endpoint: operationId, summary, request body schema, response schemas (200, 400, 401, 404, 500)
- Use `$ref` for all schema references — no inline schemas in paths
- Include `securitySchemes` and apply them globally
- Add `examples` for every schema

## Example

User: "e-commerce with users, products, orders, and reviews"

Output: Full SQL migration with 4 tables, junction tables for order_items, proper indexes, enum for order status, check constraints for rating 1-5, and both up/down migrations.

## Output Format

Write schemas using apply_patch. If generating multiple formats, write each to its own file. Always print the validation command at the end.
