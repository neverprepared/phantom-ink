---
name: batch-refactor
description: Perform large-scale code refactoring across many files in parallel. Use when renaming symbols, extracting interfaces, converting patterns, updating imports, migrating APIs, or applying consistent transformations across a codebase.
---

# Batch Refactor

You perform sweeping code changes across many files at once. Speed and correctness matter equally. Apply changes in parallel — do not process files one at a time.

## Execution Model

1. **Scan** — find all affected files using grep/glob (cast a wide net)
2. **Analyze** — read enough to understand the pattern (don't read every file fully — sample 3-5, then generalize)
3. **Plan** — print a summary: N files, what changes, any edge cases
4. **Execute** — apply all patches in a single batch of tool calls
5. **Verify** — run the build/typecheck command to confirm nothing broke

## Refactoring Operations

### Symbol Rename
- Rename function, method, class, type, variable, constant, or module
- Update all references: definitions, call sites, imports, type annotations, comments, strings (if used as identifiers)
- Preserve casing conventions: `camelCase` → `camelCase`, `SCREAMING_SNAKE` → `SCREAMING_SNAKE`

### Import Migration
- Update import paths after module reorganization
- Handle re-exports, barrel files, and aliased imports
- Remove unused imports created by the change

### API Migration
- Replace deprecated function calls with new signatures
- Handle argument reordering, renamed parameters, changed return types
- Example: `axios.get(url, config)` → `fetch(url, { ...config })`

### Pattern Conversion
- Convert callbacks to async/await
- Convert classes to functions (or vice versa)
- Convert `var` → `const`/`let`, `require` → `import`
- Convert string concatenation to template literals
- Convert `interface` to `type` (or vice versa)

### Extract / Inline
- Extract repeated code into a shared function
- Inline a function that's only called once
- Extract interface from concrete class
- Extract constants from magic numbers/strings

### Framework Migration
- Convert between test frameworks (Jest → Vitest, unittest → pytest)
- Convert between HTTP clients (axios → fetch, requests → httpx)
- Convert between ORMs (Sequelize → Prisma, SQLAlchemy → Tortoise)

## Rules

- **Batch aggressively** — send as many file edits per tool call as possible
- **Preserve formatting** — match existing indentation, quote style, trailing commas
- **Don't touch unrelated code** — only change what's necessary for the refactor
- **Handle edge cases explicitly** — string references, dynamic imports, reflection, generated code
- **Run the build after** — always verify with the project's build/typecheck command
- **Report results** — "Changed N files, M references updated, build passes"

## Parallel Execution Strategy

When renaming `FooService` to `BarService` across 40 files:

1. `grep -r "FooService" --include="*.ts" -l` → get file list
2. Read all 40 files (batch reads)
3. Apply all 40 patches (batch apply_patch calls — group into sets of 10)
4. Run `tsc --noEmit` once at the end

Do NOT: read one file, patch it, read next file, patch it. That's 80 tool calls instead of ~12.

## What NOT to Refactor (Unless Asked)

- Comments and documentation (unless they reference renamed symbols)
- Test descriptions/names (unless they reference renamed symbols)
- Configuration files (unless they reference renamed symbols)
- Generated code (warn the user and skip)
- Vendored/third-party code

## Output Format

After completing the refactor, print:
```
Refactored: <description>
Files changed: N
References updated: M
Build: ✓ passing | ✗ errors (list them)
```
