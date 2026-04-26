---
name: openapi-client
description: Generate fully typed SDK client libraries from OpenAPI/Swagger specifications. Use when the user has an OpenAPI spec (YAML or JSON) and wants a typed client in any language with auth, pagination, retries, and error handling.
---

# OpenAPI Client Generator

You generate typed SDK client libraries from OpenAPI specifications. Given a spec file or URL, produce a complete, idiomatic client library — not a thin fetch wrapper, a proper SDK.

## Execution Model

1. Read and parse the OpenAPI spec (3.x or Swagger 2.0)
2. Extract: endpoints, schemas, auth schemes, error responses
3. Determine target language from context
4. Generate the complete client library in one pass
5. Include usage examples for every resource group

## Generated Structure

```
sdk/
├── client.{ext}              # Client class with config, auth, base URL
├── types.{ext}               # All request/response types from components/schemas
├── errors.{ext}              # Typed error classes from error responses
├── auth.{ext}                # Auth providers (API key, Bearer, OAuth2)
├── pagination.{ext}          # Paginator/iterator for list endpoints
├── resources/                # One file per tag/resource group
│   ├── users.{ext}           # All /users/* endpoints
│   ├── projects.{ext}        # All /projects/* endpoints
│   └── ...
├── client_test.{ext}         # Tests with mocked HTTP responses
└── examples/                 # Usage examples
    ├── basic.{ext}
    └── pagination.{ext}
```

## Type Generation Rules

### From OpenAPI Schema → Language Type

| OpenAPI | TypeScript | Python | Go | Rust |
|---------|-----------|--------|----|------|
| `string` | `string` | `str` | `string` | `String` |
| `string` + `format: date-time` | `Date` | `datetime` | `time.Time` | `chrono::DateTime<Utc>` |
| `string` + `format: uuid` | `string` | `UUID` | `uuid.UUID` | `uuid::Uuid` |
| `string` + `format: email` | `string` | `EmailStr` | `string` | `String` |
| `string` + `format: uri` | `string` | `HttpUrl` | `string` | `url::Url` |
| `string` + `enum` | union literal | `Literal[...]` | custom type | enum |
| `integer` | `number` | `int` | `int64` | `i64` |
| `integer` + `format: int32` | `number` | `int` | `int32` | `i32` |
| `number` | `number` | `float` | `float64` | `f64` |
| `number` + `format: decimal` | `string` | `Decimal` | `decimal.Decimal` | `rust_decimal::Decimal` |
| `boolean` | `boolean` | `bool` | `bool` | `bool` |
| `array` | `T[]` | `list[T]` | `[]T` | `Vec<T>` |
| `object` + `properties` | named interface | Pydantic model | struct | struct |
| `object` + `additionalProperties` | `Record<string, V>` | `dict[str, V]` | `map[string]V` | `HashMap<String, V>` |
| `oneOf` / `anyOf` | discriminated union | `Union[...]` | interface | enum |
| nullable | `T \| null` | `T \| None` | `*T` | `Option<T>` |

### Naming Conventions
- Types: PascalCase (`CreateUserRequest`, `UserResponse`)
- Methods: language convention (camelCase TS, snake_case Python, PascalCase Go)
- Resources: plural noun matching the tag (`users`, `projects`)
- Prefix request types with operation: `Create`, `Update`, `List`, `Get`, `Delete`

## Endpoint Method Signature

Every endpoint method must include:
- All path parameters as required arguments
- Query parameters as an optional options object/struct
- Request body as a typed argument (for POST/PUT/PATCH)
- Return type matching the success response schema
- Doc comment with the `summary` from the spec

### TypeScript Example
```typescript
/**
 * List all users with optional filtering
 * @see GET /users
 */
async listUsers(options?: ListUsersOptions): Promise<PaginatedResponse<User>> {
  return this.request<PaginatedResponse<User>>({
    method: 'GET',
    path: '/users',
    query: options,
  });
}

/**
 * Create a new user
 * @see POST /users
 */
async createUser(data: CreateUserRequest): Promise<User> {
  return this.request<User>({
    method: 'POST',
    path: '/users',
    body: data,
  });
}

/**
 * Get a user by ID
 * @see GET /users/{userId}
 */
async getUser(userId: string): Promise<User> {
  return this.request<User>({
    method: 'GET',
    path: `/users/${userId}`,
  });
}
```

## Pagination

Detect pagination pattern from the spec and generate an async iterator:

```typescript
// Auto-paginate
for await (const user of client.users.listAll({ role: 'admin' })) {
  console.log(user.email);
}

// Manual pagination
const page = await client.users.list({ page: 1, limit: 20 });
console.log(page.data, page.total, page.hasMore);
```

Support patterns: offset/limit, cursor-based, link-header, next-token.

## Error Handling

Generate typed errors from error response schemas:

```typescript
try {
  await client.users.create(data);
} catch (e) {
  if (e instanceof ValidationError) {
    console.log(e.errors); // typed field errors
  } else if (e instanceof NotFoundError) {
    console.log(e.message);
  } else if (e instanceof RateLimitError) {
    console.log(e.retryAfter); // seconds
  }
}
```

Map HTTP status codes to error classes:
- 400 → `ValidationError`
- 401 → `AuthenticationError`
- 403 → `AuthorizationError`
- 404 → `NotFoundError`
- 409 → `ConflictError`
- 422 → `UnprocessableError`
- 429 → `RateLimitError`
- 5xx → `ServerError`

## Rules

- **Cover every endpoint** — no skipping obscure endpoints
- **Use $ref resolution** — inline all schema references
- **Handle optional fields** — nullable and optional are different
- **Default values** — set defaults from schema `default` property
- **Discriminated unions** — use `discriminator` field when present
- **File uploads** — handle `multipart/form-data` with proper typing
- **Streaming** — handle `text/event-stream` responses with event types

## Output Format

Write all files using apply_patch. Include a README.md with installation and quick-start (3 operations). Print the total endpoint count and type count at the end.
