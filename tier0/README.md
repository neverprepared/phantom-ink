# Tier-0 local ollama harness

The private, local **Tier-0** brain in the platform vision: **opencode** driving a
**local ollama** model for inference (nothing leaves the box), using the
**phantom-ink MCP gateway** for credentialed, per-profile, scoped tools.

This is config + a thin launcher — opencode owns the agent loop. We add no
custom agent code (ADR-002 philosophy: standard tools, minimal custom routing).

```
opencode (local)  ──model──▶  ollama  (localhost:11434, private)
        │
        └──MCP (remote, Bearer)──▶  /gateway/mcp  ──▶  per-profile scoped tools
```

## Prerequisites

- **opencode** installed (`npm install -g opencode-ai`, or `brew install anomalyco/tap/opencode`, or `curl -fsSL https://opencode.ai/install | bash`)
- **ollama** running locally with a tool-capable model (e.g. `qwen3:8b`)
- **CL_API_KEY** in your shell — the brainbox operator key (used to mint a gateway token)
- The brainbox MCP gateway configured + unlocked (`CL_GATEWAY__{SECRET_KEY,CATALOG_PATH,SERVERS}`), with the profile's creds stored (app Profiles panel)

## Use

```bash
just tier0-opencode                 # profile from $WORKSPACE_PROFILE, else "personal"
just tier0-opencode personal        # explicit profile
# or directly:
tier0/opencode-launch.sh -p personal
tier0/opencode-launch.sh -p personal run "convert this page to markdown: <uri>"   # headless
```

The launcher mints a **Tier-0 gateway token** scoped to the chosen profile, renders an
opencode config to `~/.cache/phantom-ink/opencode/opencode.<profile>.json`, and execs
opencode. The token is injected at runtime via `{env:PHANTOM_GW_TOKEN}` — it is **never
written into the config file**.

## Env knobs

| Var | Default | Purpose |
|-----|---------|---------|
| `CL_API_KEY` | — (required) | brainbox operator key, to mint the token |
| `BRAINBOX_URL` | `https://brainbox-api.neverprepared.com` | base for minting + the gateway endpoint |
| `OPENCODE_MODEL` | `ollama/qwen3:8b` | opencode model id (`<provider>/<model>`) |
| `OLLAMA_OPENAI_URL` | `http://localhost:11434/v1` | ollama OpenAI-compatible endpoint |
| `PHANTOM_GW_TTL` | `43200` (12h) | minted token lifetime, seconds |
| `PHANTOM_GW_SCOPE` | empty (all tools) | comma-separated `<server>__<tool>` patterns |

## Notes

- **Per-profile isolation:** the token determines which profile's stored creds the gateway
  injects into downstream MCP servers. `personal` and `work` get different secrets under the
  same tool names.
- **Which tools appear** is the gateway operator allowlist (`CL_GATEWAY__SERVERS`) ∩ the
  token scope (`PHANTOM_GW_SCOPE`).
- To add ollama models beyond the two listed, edit `opencode.template.json`'s
  `provider.ollama.models` map (opencode needs custom-provider models declared).
