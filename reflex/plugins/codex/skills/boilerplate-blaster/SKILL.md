---
name: boilerplate-blaster
description: Generate repetitive but correct code — CRUD endpoints, form components, model classes, serializers, validators, admin interfaces, and other structural code. Use when the user needs many similar components or endpoints generated from a data model.
---

# Boilerplate Blaster

You generate large volumes of repetitive, correct code from data model descriptions. Given a set of entities and their fields, produce every layer of the application stack in one pass.

## Execution Model

1. Parse the entity/model descriptions
2. Determine the tech stack from project context
3. Generate all layers for all entities at once
4. No placeholders, no TODOs — every method fully implemented

## What to Generate Per Entity

### Backend
- **Model/Entity** — ORM class with all fields, types, relationships, validations
- **Repository/DAO** — CRUD operations (create, get by ID, list with pagination, update, delete)
- **Service** — Business logic layer wrapping repository (validation, authorization hooks, events)
- **Controller/Handler** — HTTP endpoints (POST, GET, GET/:id, PUT/:id, DELETE/:id)
- **DTOs** — Create, Update, Response, List schemas (separate from ORM model)
- **Validation** — Input validation rules per field
- **Tests** — Unit tests for service, integration tests for endpoints

### Frontend (if requested)
- **Type definitions** — matching backend DTOs
- **API client** — typed functions calling each endpoint
- **List component** — table/grid with pagination, sorting, filtering
- **Detail component** — read-only view of a single entity
- **Form component** — create/edit form with validation
- **Delete confirmation** — modal/dialog with confirmation

## Stack-Specific Patterns

### FastAPI + SQLAlchemy + Pydantic
```python
# models/user.py
class User(Base):
    __tablename__ = "users"
    id = Column(UUID, primary_key=True, default=uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.MEMBER)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

# schemas/user.py
class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    role: UserRole = UserRole.MEMBER

class UserUpdate(BaseModel):
    email: EmailStr | None = None
    name: str | None = Field(None, min_length=1, max_length=100)
    role: UserRole | None = None

class UserResponse(BaseModel):
    id: UUID
    email: str
    name: str
    role: UserRole
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# routers/users.py
@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db)):
    ...
```

### Express + Prisma + TypeScript
```typescript
// routes/users.ts
router.post('/', validate(createUserSchema), async (req, res) => {
  const user = await prisma.user.create({ data: req.body });
  res.status(201).json(user);
});

router.get('/', async (req, res) => {
  const { page = 1, limit = 20, sort = 'createdAt', order = 'desc' } = req.query;
  const [users, total] = await Promise.all([
    prisma.user.findMany({
      skip: (page - 1) * limit,
      take: limit,
      orderBy: { [sort]: order },
    }),
    prisma.user.count(),
  ]);
  res.json({ data: users, total, page, limit });
});
```

### Go + Chi + sqlc
```go
// handlers/users.go
func (h *UserHandler) Create(w http.ResponseWriter, r *http.Request) {
    var req CreateUserRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        respondError(w, http.StatusBadRequest, err)
        return
    }
    if err := req.Validate(); err != nil {
        respondError(w, http.StatusUnprocessableEntity, err)
        return
    }
    user, err := h.queries.CreateUser(r.Context(), sqlc.CreateUserParams{
        Email: req.Email,
        Name:  req.Name,
        Role:  req.Role,
    })
    if err != nil {
        respondError(w, http.StatusInternalServerError, err)
        return
    }
    respondJSON(w, http.StatusCreated, user)
}
```

## Pagination Pattern

Always implement cursor-based pagination for list endpoints:
```
GET /users?cursor=abc123&limit=20&sort=created_at&order=desc

Response:
{
  "data": [...],
  "next_cursor": "def456",
  "has_more": true,
  "total": 1523
}
```

Fall back to offset pagination only if the user explicitly requests it.

## Field Type Inference

When the user provides field names without types, infer:
| Field Name Pattern | Type | Validation |
|-------------------|------|------------|
| `*email*` | string | email format |
| `*url*`, `*link*` | string | URL format |
| `*phone*` | string | E.164 format |
| `*at`, `*_date` | datetime | ISO 8601 |
| `*count*`, `*quantity*` | integer | >= 0 |
| `*price*`, `*amount*`, `*cost*` | decimal(10,2) | >= 0 |
| `*name*`, `*title*` | string(255) | non-empty |
| `*description*`, `*body*`, `*content*` | text | — |
| `*status*`, `*type*`, `*role*` | enum | defined values |
| `*flag*`, `is_*`, `has_*` | boolean | — |
| `*id` | uuid | — |
| `*image*`, `*avatar*`, `*photo*` | string | URL format |
| `*password*` | string | min 8 chars, hashed on write |

## Rules

- **No generic error handling** — catch specific errors (duplicate key, not found, validation)
- **Soft delete by default** — add `deleted_at` column, filter in queries (unless user says hard delete)
- **Audit fields always** — `created_at`, `updated_at`, `created_by` on every entity
- **Index foreign keys** — every FK gets an index
- **Consistent naming** — pluralized table names, singular model names, RESTful route names

## Output Format

Write all files using apply_patch. Batch aggressively — all models in one call, all routes in another. Print the file list and any setup commands at the end.
