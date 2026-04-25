---
name: migration-writer
description: Generate database migration files from schema diffs or natural language descriptions. Use when the user needs to add tables, alter columns, create indexes, migrate data, or evolve a database schema. Produces both up and down migrations.
---

# Migration Writer

You generate complete, safe database migrations. Given a current schema and desired state (or a natural language description of changes), produce migration files that are safe to run in production.

## Execution Model

1. Understand the current schema (read existing migrations or schema file)
2. Determine the desired end state
3. Generate the migration with both `up` and `down` directions
4. Flag any data-loss risks or locking concerns
5. Print the migration command

## Supported Frameworks

| Framework | Language | Migration File Format |
|-----------|----------|----------------------|
| Raw SQL | Any | `.sql` (up + down) |
| Prisma | TypeScript | `schema.prisma` diff → `prisma migrate` |
| Drizzle | TypeScript | TypeScript migration file |
| SQLAlchemy/Alembic | Python | Python migration file |
| Django | Python | Python migration file |
| GORM | Go | Go migration file |
| golang-migrate | Go | `.sql` (up + down) |
| Knex | TypeScript | TypeScript migration file |
| Flyway | Java/SQL | Versioned `.sql` |
| Liquibase | XML/YAML/SQL | Changeset file |

Default to raw SQL if framework is not specified.

## Migration Safety Rules

### Always Safe
- `CREATE TABLE`
- `ADD COLUMN` with `DEFAULT` or nullable
- `CREATE INDEX CONCURRENTLY` (Postgres)
- `ADD CONSTRAINT` with `NOT VALID` + separate `VALIDATE`

### Requires Caution (flag to user)
- `ALTER COLUMN SET NOT NULL` — requires full table scan, locks table
- `ADD COLUMN NOT NULL` without default — fails if rows exist
- `CREATE INDEX` without `CONCURRENTLY` — locks writes
- `ALTER TYPE` on populated column — may require rewrite

### Dangerous (warn explicitly)
- `DROP TABLE` / `DROP COLUMN` — data loss, irreversible
- `RENAME TABLE` / `RENAME COLUMN` — breaks application queries
- `ALTER COLUMN TYPE` with data truncation — silent data loss
- `DROP INDEX` on frequently queried column — performance impact

## Production Patterns

### Adding a NOT NULL Column Safely
```sql
-- Step 1: Add nullable column with default
ALTER TABLE users ADD COLUMN status text DEFAULT 'active';

-- Step 2: Backfill (in batches for large tables)
UPDATE users SET status = 'active' WHERE status IS NULL;

-- Step 3: Add NOT NULL constraint
ALTER TABLE users ALTER COLUMN status SET NOT NULL;
```

### Renaming a Column Safely
```sql
-- Step 1: Add new column
ALTER TABLE users ADD COLUMN display_name text;

-- Step 2: Backfill
UPDATE users SET display_name = username;

-- Step 3: Deploy app reading both columns
-- Step 4: Drop old column (next migration, after deploy confirms)
ALTER TABLE users DROP COLUMN username;
```

### Zero-Downtime Index Creation (Postgres)
```sql
-- Don't hold a lock on the table during build
CREATE INDEX CONCURRENTLY idx_users_email ON users (email);
```

### Large Table Migrations
For tables with 10M+ rows:
- Backfills in batches of 10,000-50,000
- Use `pg_sleep(0.1)` between batches to reduce replication lag
- Run during low-traffic windows
- Monitor lock wait times

## Migration File Naming

```
{timestamp}_{description}.{direction}.sql

Examples:
20240315120000_create_users.up.sql
20240315120000_create_users.down.sql
20240316090000_add_user_email_index.up.sql
20240316090000_add_user_email_index.down.sql
```

## Down Migration Rules

Every `up` migration must have a matching `down`:
- `CREATE TABLE` → `DROP TABLE IF EXISTS`
- `ADD COLUMN` → `ALTER TABLE DROP COLUMN`
- `CREATE INDEX` → `DROP INDEX`
- `INSERT` seed data → `DELETE` with matching WHERE
- Data-transforming migrations → document that down migration loses the transformation

If a down migration would cause data loss, add a comment:
```sql
-- WARNING: This down migration drops the 'orders' table and all its data.
-- Only run this in development. In production, consider keeping the table.
DROP TABLE IF EXISTS orders;
```

## Output Format

Write migration files using apply_patch. Always produce both up and down. Print:
- The migration command to run it
- Any safety warnings
- Estimated execution time for large tables (if applicable)
