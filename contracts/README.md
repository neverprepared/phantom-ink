# Contracts

Output contracts for phantom-ink collection scripts and integrations.

| File | Used by |
|------|---------|
| `timeline-entry.schema.json` | Scheduler collection scripts → collected_entries store → timeline panel, metric widgets |

## Writing a collection script

A collection script is any executable that writes a JSON array conforming to `timeline-entry.schema.json` to stdout and exits zero on success.

```sh
#!/bin/bash
# Output contract: contracts/timeline-entry.schema.json

# ... fetch data, map fields, print JSON array ...
echo '[{"id":"my-metric","kind":"metric","title":"My Value","value":"42","status":"active"}]'
```

Scripts run via `direnv exec <workspace_home>` so profile environment variables (including 1Password-backed secrets) are available.

## Validating output

```sh
# requires: npm install -g ajv-cli
just validate-output ./path/to/script.sh
```
