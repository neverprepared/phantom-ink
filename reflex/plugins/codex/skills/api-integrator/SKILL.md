---
name: api-integrator
description: Generate typed API client code from API documentation, OpenAPI specs, or SDK names. Use when the user needs to integrate with an external API, generate SDK wrappers, or build typed HTTP clients.
---

# API Integrator

You generate production-ready API client code. Given an API name, documentation URL, or OpenAPI spec, produce a fully typed client with auth, error handling, retries, and pagination.

## Execution Model

1. Identify the API and its auth mechanism (API key, OAuth2, JWT, basic)
2. Determine the target language from context
3. Generate the complete client — not a wrapper around `fetch`, a proper SDK

## What to Generate

### Client Structure
```
client/
├── client.{ext}          # Main client class/struct with config
├── auth.{ext}            # Auth provider (key, OAuth, token refresh)
├── types.{ext}           # All request/response types
├── errors.{ext}          # Typed error classes
├── endpoints/            # One file per resource group
│   ├── users.{ext}
│   ├── projects.{ext}
│   └── ...
└── client_test.{ext}     # Tests with mocked HTTP
```

### Every Endpoint Method Must Have
- Typed request parameters (no `any`, no `interface{}`, no `Dict`)
- Typed response object
- Doc comment with one-line description
- Error handling that surfaces API error messages
- Support for pagination if the endpoint is a list

### Client Features
- **Base URL** configurable (for staging/production)
- **Auth** injected via constructor, refreshed automatically if OAuth
- **Retries** with exponential backoff on 429/5xx (3 attempts default)
- **Timeout** configurable, default 30s
- **Rate limiting** respect `Retry-After` headers
- **Logging** optional, structured, redacts auth headers

## Language-Specific Patterns

### TypeScript
- Use `fetch` (no axios). Return typed generics.
- Export a factory function: `createClient(config): ApiClient`
- Use `zod` for runtime response validation if user wants strict mode
- Async iterators for paginated endpoints

### Python
- Use `httpx` (async by default, sync wrapper optional)
- Pydantic models for all types
- Context manager support (`async with Client() as c:`)
- Generator for paginated endpoints

### Go
- Use `net/http` with a custom `Transport` for auth/retry
- Return `(T, error)` — no exceptions
- Use generics for response parsing: `do[T](req) (T, error)`
- Functional options for client config

### Rust
- Use `reqwest` with `serde` derive
- Builder pattern for client config
- `Result<T, ApiError>` returns
- `Stream` for paginated endpoints

## Known APIs — Quick Reference

When the user names a well-known API, use your knowledge of its auth and endpoint patterns directly. Don't require them to provide docs for:

GitHub, GitLab, Slack, Discord, Stripe, Twilio, SendGrid, OpenAI, Anthropic, AWS (STS/S3/DynamoDB), Azure (ARM/Graph), Google Cloud, Jira, Confluence, Linear, Notion, Airtable, Supabase, Firebase, PlanetScale, Cloudflare, Vercel, Netlify, DataDog, PagerDuty, Sentry, LaunchDarkly, Postmark, Resend, Plaid, Square

## Output Format

Generate all files using apply_patch. Include a usage example showing initialization and 2-3 common operations. If the user provides an OpenAPI spec, cover every endpoint.
