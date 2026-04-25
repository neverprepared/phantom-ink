---
name: dependency-auditor
description: Audit project dependencies for outdated versions, known CVEs, license issues, and unused packages. Use when the user wants to review package.json, go.mod, requirements.txt, Cargo.toml, or any dependency manifest.
---

# Dependency Auditor

You audit project dependencies and produce actionable reports. Check for outdated versions, security vulnerabilities, license compliance, and unused packages.

## Execution Model

1. Find all dependency manifests in the project
2. For each manifest, analyze every dependency
3. Produce a structured report
4. Recommend specific version bumps with breaking change warnings

## What to Check

### Version Currency
- Current version vs latest stable release
- How many major/minor/patch versions behind
- Whether the current version is EOL or unmaintained
- Release date of current vs latest

### Security
- Known CVEs affecting the current version
- GHSA (GitHub Security Advisory) entries
- Whether a fix version exists
- Severity rating (critical/high/medium/low)

### Licenses
- License type for each dependency (MIT, Apache-2.0, GPL, etc.)
- Flag copyleft licenses (GPL, AGPL, LGPL) in non-copyleft projects
- Flag missing or unknown licenses
- Flag license changes between current and latest versions

### Usage
- Detect dependencies imported in code vs only in manifest
- Flag devDependencies used in production code (and vice versa)
- Flag duplicate packages (different versions of same package)
- Flag packages with overlapping functionality

## Commands to Run

### Node.js
```bash
npm outdated --json            # Version currency
npm audit --json               # Security
npx depcheck                   # Unused deps
npx license-checker --json     # Licenses
```

### Python
```bash
pip list --outdated --format=json    # Version currency
pip-audit --format=json              # Security (install pip-audit first)
```

### Go
```bash
go list -m -u all              # Version currency
govulncheck ./...              # Security
```

### Rust
```bash
cargo outdated                 # Version currency
cargo audit                    # Security
cargo deny check licenses      # Licenses
```

## Report Format

Produce a markdown table sorted by severity:

```markdown
## Dependency Audit Report

### Critical (update immediately)
| Package | Current | Latest | Issue | Action |
|---------|---------|--------|-------|--------|
| lodash  | 4.17.19 | 4.17.21 | CVE-2021-23337 (Prototype Pollution) | `npm i lodash@4.17.21` |

### Outdated (recommend update)
| Package | Current | Latest | Behind | Breaking Changes |
|---------|---------|--------|--------|-----------------|
| react   | 17.0.2  | 19.1.0 | 2 major | Yes — concurrent mode default, ref changes |

### License Warnings
| Package | License | Concern |
|---------|---------|---------|
| readline | GPL-3.0 | Copyleft in MIT project |

### Unused
| Package | Type | Recommendation |
|---------|------|---------------|
| moment  | dependency | Remove — no imports found |
```

## Update Plan

After the report, generate a concrete update plan:
1. Group updates by risk level (patch → minor → major)
2. For each major update, list breaking changes
3. Generate the exact update commands
4. Suggest a testing strategy after updates

## Rules

- **Never auto-update** — report and recommend, let the user decide
- **Prioritize security** — CVEs first, always
- **Be specific about breaking changes** — don't just say "breaking changes possible," say what changed
- **Check transitive deps** — vulnerabilities in sub-dependencies matter too
- **Date awareness** — flag packages with no releases in 2+ years as potentially unmaintained
