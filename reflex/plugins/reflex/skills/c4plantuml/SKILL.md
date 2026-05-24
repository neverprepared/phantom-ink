---
name: c4plantuml
description: C4-PlantUML syntax for C4 model architecture diagrams using PlantUML — context, container, component, and dynamic views with C4 macros and standard styling.
---

# C4-PlantUML

C4-PlantUML combines PlantUML with C4 model macros to produce standardized architecture diagrams at four levels of abstraction.

## Rendering

```
convert_diagram("c4plantuml", source, "svg")
convert_diagram("c4plantuml", source, "png")
```

No companion required.

## The Four C4 Levels

| Level | Include | Shows |
|-------|---------|-------|
| Context | `C4_Context.puml` | System and its users/external systems |
| Container | `C4_Container.puml` | Apps, services, databases inside the system |
| Component | `C4_Component.puml` | Components inside a container |
| Dynamic | `C4_Dynamic.puml` | Runtime interaction sequence |
| Deployment | `C4_Deployment.puml` | Infrastructure and containers deployed to it |

## System Context Diagram

```plantuml
@startuml
!include C4_Context.puml

title System Context — Payment Platform

Person(customer, "Customer", "Makes purchases")
Person(support, "Support Agent", "Handles disputes")

System(platform, "Payment Platform", "Processes payments and manages accounts")

System_Ext(bank, "Banking Network", "Card authorization and settlement")
System_Ext(email, "Email Service", "Sends receipts and alerts")
System_Ext(fraud, "Fraud Detection", "External fraud scoring API")

Rel(customer, platform, "Makes payments via", "HTTPS")
Rel(support, platform, "Manages disputes via", "HTTPS")
Rel(platform, bank, "Authorizes cards via", "ISO 8583")
Rel(platform, email, "Sends notifications via", "SMTP")
Rel(platform, fraud, "Scores transactions via", "REST/JSON")

SHOW_LEGEND()
@enduml
```

## Container Diagram

```plantuml
@startuml
!include C4_Container.puml

title Containers — Payment Platform

Person(customer, "Customer", "")

System_Boundary(platform, "Payment Platform") {
    Container(spa, "Single Page App", "React/TypeScript", "Customer-facing checkout UI")
    Container(api, "API Gateway", "FastAPI/Python", "REST API, auth, rate limiting")
    Container(paymentSvc, "Payment Service", "Python", "Payment processing logic")
    Container(webhookSvc, "Webhook Service", "Python", "Outbound event delivery")
    ContainerDb(db, "Primary Database", "PostgreSQL", "Transactions, accounts, audit log")
    ContainerDb(cache, "Cache", "Redis", "Session store, idempotency keys")
    ContainerQueue(queue, "Message Queue", "RabbitMQ", "Async job dispatch")
}

System_Ext(bank, "Banking Network", "")
System_Ext(email, "Email Service", "")

Rel(customer, spa, "Uses", "HTTPS")
Rel(spa, api, "Calls", "REST/JSON/HTTPS")
Rel(api, paymentSvc, "Routes to", "gRPC")
Rel(api, cache, "Reads/writes", "Redis")
Rel(paymentSvc, db, "Reads/writes", "SQL")
Rel(paymentSvc, queue, "Publishes to", "AMQP")
Rel(webhookSvc, queue, "Consumes from", "AMQP")
Rel(paymentSvc, bank, "Authorizes via", "ISO 8583")
Rel(webhookSvc, email, "Triggers", "SMTP")

SHOW_LEGEND()
@enduml
```

## Component Diagram

```plantuml
@startuml
!include C4_Component.puml

title Components — API Gateway

Container_Ext(spa, "Single Page App", "React", "")
ContainerDb_Ext(db, "Database", "PostgreSQL", "")
ContainerDb_Ext(cache, "Cache", "Redis", "")

Container_Boundary(api, "API Gateway") {
    Component(router, "Request Router", "FastAPI", "Routes requests to handlers")
    Component(auth, "Auth Middleware", "Python", "Validates JWT, extracts claims")
    Component(rateLimit, "Rate Limiter", "Python", "Per-user and per-IP limits")
    Component(payHandler, "Payment Handler", "Python", "Handles payment endpoints")
    Component(acctHandler, "Account Handler", "Python", "Handles account endpoints")
    Component(validator, "Request Validator", "Pydantic", "Schema validation")
}

Rel(spa, router, "Sends requests to", "REST/HTTPS")
Rel(router, auth, "Authenticates via")
Rel(router, rateLimit, "Rate-checks via")
Rel(router, payHandler, "Routes payment requests to")
Rel(router, acctHandler, "Routes account requests to")
Rel(payHandler, validator, "Validates with")
Rel(acctHandler, validator, "Validates with")
Rel(payHandler, db, "Reads/writes", "SQL")
Rel(acctHandler, cache, "Reads/writes", "Redis")

@enduml
```

## Dynamic Diagram (Sequence)

```plantuml
@startuml
!include C4_Dynamic.puml

title Dynamic — Payment Authorization Flow

Person(customer, "Customer", "")
Container(spa, "SPA", "React", "")
Container(api, "API Gateway", "FastAPI", "")
Container(paymentSvc, "Payment Service", "Python", "")
System_Ext(bank, "Banking Network", "")

Rel(customer, spa, "1", "Submits payment form")
Rel(spa, api, "2", "POST /payments")
Rel(api, paymentSvc, "3", "ProcessPayment(request)")
Rel(paymentSvc, bank, "4", "Authorize card")
Rel_Back(paymentSvc, bank, "5", "Authorization code")
Rel_Back(api, paymentSvc, "6", "Payment result")
Rel_Back(spa, api, "7", "201 Created")
Rel_Back(customer, spa, "8", "Confirmation screen")

@enduml
```

## Deployment Diagram

```plantuml
@startuml
!include C4_Deployment.puml

title Deployment — Production

Deployment_Node(aws, "AWS", "Amazon Web Services") {
    Deployment_Node(region, "us-east-1", "Region") {
        Deployment_Node(ecs, "ECS Cluster", "Amazon ECS") {
            Container(api, "API Gateway", "FastAPI", "2 tasks, 0.5 vCPU each")
            Container(worker, "Worker", "Celery", "4 tasks, 1 vCPU each")
        }
        Deployment_Node(rds, "RDS Multi-AZ", "Amazon RDS") {
            ContainerDb(db, "PostgreSQL", "PostgreSQL 15", "db.r6g.large")
        }
        Deployment_Node(elasticache, "ElastiCache", "Amazon ElastiCache") {
            ContainerDb(redis, "Redis", "Redis 7", "cache.r6g.large")
        }
    }
}

Rel(api, db, "Reads/writes", "SQL/5432")
Rel(api, redis, "Caches", "Redis/6379")
Rel(worker, db, "Reads/writes", "SQL/5432")

@enduml
```

## Macro Reference

### People and Systems
```
Person(alias, "Label", "Description")
Person_Ext(alias, "Label", "Description")        # external/grey
System(alias, "Label", "Description")
System_Ext(alias, "Label", "Description")        # external/grey
System_Boundary(alias, "Label") { ... }
```

### Containers
```
Container(alias, "Label", "Tech", "Description")
ContainerDb(alias, "Label", "Tech", "Description")
ContainerQueue(alias, "Label", "Tech", "Description")
Container_Ext(alias, "Label", "Tech", "Description")
Container_Boundary(alias, "Label") { ... }
```

### Components
```
Component(alias, "Label", "Tech", "Description")
ComponentDb(alias, "Label", "Tech", "Description")
Component_Ext(alias, "Label", "Tech", "Description")
```

### Relationships
```
Rel(from, to, "Label")
Rel(from, to, "Label", "Technology")
Rel_Back(from, to, "Label")               # arrow goes backward
Rel_Neighbor(from, to, "Label")           # hint for side-by-side layout
BiRel(from, to, "Label")                  # bidirectional
```

### Layout Helpers
```
LAYOUT_TOP_DOWN()     # default
LAYOUT_LEFT_RIGHT()
LAYOUT_WITH_LEGEND()
SHOW_LEGEND()
```

## Structurizr vs C4-PlantUML

| Feature | c4plantuml | structurizr |
|---------|------------|-------------|
| Syntax style | PlantUML macros | Purpose-built DSL |
| Multiple views | One view per file | Multiple views in one workspace |
| Styling | PlantUML skinparam | Style blocks |
| Filtering | Manual | Tag-based `include`/`exclude` |
| Familiarity | PlantUML users | Architecture-first |

**Use c4plantuml when:** You know PlantUML, want quick C4 diagrams, one view at a time.
**Use structurizr when:** You want to define the full model once and generate multiple views.

## See Also
- `structurizr` skill — full C4 DSL with multi-view workspaces
- `plantuml` skill — general PlantUML for non-C4 UML diagrams
