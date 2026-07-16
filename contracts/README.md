# Contracts

Output contract for phantom-ink collection scripts.

| File | Purpose |
|------|---------|
| `collection-output.schema.json` | Array framing for a collection script's stdout — `$ref`s the canonical timeline-entry item schema by `$id`. |

The **item** shape (one timeline entry) is not stored here. It lives in the
generated, pinned contract at `app/internal/contract/timeline-entry.schema.json`,
fetched from `neverprepared/phantom-contracts` at the tag in
`app/internal/contract/CONTRACT_TAG` (run `just app-contract-gen` to refresh it).
That one file is the single source for both the Go bindings (via codegen) and
this validation recipe — collection scripts just emit an **array** of entries,
which `collection-output.schema.json` layers on top.

> Note: a collection entry may carry a `value` field (for `kind=metric`). The
> canonical agent-bus envelope dropped `value` in v2.1, but the object schema
> leaves `additionalProperties` open, so `value` still validates. The bus and
> collection scripts share the envelope shape; only collection output is an array
> and only it uses `value`.

## Writing a collection script

A collection script is any executable that writes a JSON array conforming to the
timeline-entry contract to stdout and exits zero on success.

```sh
#!/bin/bash
# Output contract: array of app/internal/contract/timeline-entry.schema.json

# ... fetch data, map fields, print JSON array ...
echo '[{"id":"my-metric","kind":"metric","title":"My Value","value":"42","status":"active"}]'
```

Scripts run via `direnv exec <workspace_home>` so profile environment variables (including 1Password-backed secrets) are available.

## Validating output

```sh
# requires: npm install -g ajv-cli
just validate-output ./path/to/script.sh
```
