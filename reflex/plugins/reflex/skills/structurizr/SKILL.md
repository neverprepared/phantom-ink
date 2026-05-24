---
name: structurizr
description: Structurizr DSL syntax for C4 model architecture diagrams — workspace, system context, container, component, and deployment views with full relationship modeling.
---

# Structurizr DSL

Structurizr DSL is the canonical way to write C4 model architecture diagrams. Define your model once, render multiple views.

## Rendering

```
convert_diagram("structurizr", source, "svg")
convert_diagram("structurizr", source, "png")
```

No companion required.

## Core Structure

```
workspace "Name" "Description" {
    model {
        # Define people, systems, containers, components
    }
    views {
        # Define which views to render
    }
}
```

## Model Elements

```
workspace {
    model {
        # Person
        user = person "End User" "A user of the system"
        admin = person "Administrator" "Manages the system" {
            tags "Admin"
        }

        # Software System
        mySystem = softwareSystem "My System" "Does things" {
            # Containers (inside a software system)
            webapp = container "Web App" "React SPA" "React/TypeScript" {
                # Components (inside a container)
                authComp = component "Auth Component" "Handles login" "JWT"
                apiClient = component "API Client" "Calls backend" "fetch"
            }

            api = container "API" "REST backend" "FastAPI/Python"
            db = container "Database" "Stores data" "PostgreSQL" {
                tags "Database"
            }
        }

        # External systems
        emailSvc = softwareSystem "Email Service" "Sends emails" {
            tags "External"
        }
    }
}
```

## Relationships

```
model {
    user -> mySystem "Uses" "HTTPS"
    user -> webapp "Opens" "Browser"
    webapp -> api "Calls" "REST/JSON"
    api -> db "Reads/writes" "SQL"
    api -> emailSvc "Sends emails via" "SMTP"
    authComp -> apiClient "Provides token to"
}
```

Relationship syntax: `source -> destination "description" "technology"`

## Views

```
views {
    # System Context: one software system at center
    systemContext mySystem "SystemContext" {
        include *
        autoLayout lr
    }

    # Container view: all containers in a system
    container mySystem "Containers" {
        include *
        autoLayout tb
    }

    # Component view: all components in a container
    component webapp "WebAppComponents" {
        include *
        autoLayout lr
    }

    # Deployment view
    deployment mySystem "Production" "ProductionDeployment" {
        include *
        autoLayout tb
    }

    theme default
}
```

## Deployment Model

```
model {
    production = deploymentEnvironment "Production" {
        awsRegion = deploymentNode "AWS us-east-1" "" "Amazon Web Services" {
            tags "Amazon Web Services - Region"

            ecs = deploymentNode "ECS Cluster" "" "Amazon ECS" {
                apiInstance = containerInstance api
            }

            rds = deploymentNode "RDS" "" "Amazon RDS" {
                dbInstance = containerInstance db
            }

            cf = infrastructureNode "CloudFront" "CDN" "Amazon CloudFront"
        }
    }
}
```

## Styling

```
views {
    styles {
        element "Person" {
            shape Person
            background #08427B
            color #ffffff
        }
        element "Software System" {
            background #1168BD
            color #ffffff
        }
        element "Container" {
            background #438DD5
            color #ffffff
        }
        element "Component" {
            background #85BBF0
            color #000000
        }
        element "Database" {
            shape Cylinder
        }
        element "External" {
            background #999999
            color #ffffff
        }
        relationship "Relationship" {
            dashed false
        }
    }
}
```

Shapes: `Box`, `Circle`, `Component`, `Cylinder`, `Ellipse`, `Hexagon`, `Person`, `Pipe`, `RoundedBox`, `WebBrowser`

## Tags and Filtering

```
model {
    api = container "API" {
        tags "Critical" "Internal"
    }
}

views {
    container mySystem {
        include element.tag==Critical
        exclude element.tag==Internal
    }
}
```

## Complete Example: SaaS Platform

```
workspace "SaaS Platform" "Multi-tenant SaaS" {
    model {
        customer = person "Customer" "A paying user"
        support = person "Support Agent" "Internal support"

        platform = softwareSystem "Platform" "Core SaaS product" {
            spa = container "Single Page App" "Customer UI" "React"
            adminUi = container "Admin UI" "Support UI" "React"
            api = container "API Gateway" "REST API" "FastAPI"
            authSvc = container "Auth Service" "JWT + OAuth2" "Python"
            workerSvc = container "Worker" "Background jobs" "Celery"
            db = container "PostgreSQL" "Primary store" "PostgreSQL" {
                tags "Database"
            }
            cache = container "Redis" "Cache + queues" "Redis" {
                tags "Cache"
            }
        }

        stripe = softwareSystem "Stripe" "Payment processing" {
            tags "External"
        }

        customer -> spa "Uses" "HTTPS"
        support -> adminUi "Uses" "HTTPS"
        spa -> api "Calls" "REST/JSON"
        adminUi -> api "Calls" "REST/JSON"
        api -> authSvc "Authenticates via"
        api -> db "Reads/writes" "SQL"
        api -> cache "Caches via" "Redis protocol"
        api -> workerSvc "Enqueues jobs" "Redis"
        api -> stripe "Charges customers" "HTTPS"
    }

    views {
        systemContext platform "Context" {
            include *
            autoLayout lr
        }

        container platform "Containers" {
            include *
            autoLayout tb
        }

        theme default
    }
}
```

## Tips

- `include *` in a view includes all elements reachable from the view's scope
- `autoLayout` directions: `tb` (top-bottom), `bt`, `lr` (left-right), `rl`
- Tags drive both styling and filtering — add them liberally
- One workspace can define multiple views; Kroki renders the first view defined
- For C4 with PlantUML syntax instead of DSL, use the `c4plantuml` type

## See Also
- `c4plantuml` skill — C4 model using PlantUML syntax
- `plantuml` skill — general UML diagrams
