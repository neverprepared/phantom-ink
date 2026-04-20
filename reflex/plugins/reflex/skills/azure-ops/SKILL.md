---
name: azure-ops
description: Azure operations not covered by the Microsoft MCP — PIM role activation, support tickets, resource locks, cost management, and policy compliance
---

# Azure Ops Skill

> CLI patterns for Azure operations that the Microsoft MCP server doesn't expose.

## When to Use

- Activating Privileged Identity Management (PIM) eligible roles
- Opening or managing Azure support tickets
- Managing resource locks (prevent accidental deletion)
- Querying cost/billing data
- Checking Azure Policy compliance status

## Prerequisites

```bash
# Ensure logged in
az account show || az login

# Required extensions
az extension add --name support 2>/dev/null || true

# Verify PIM access (requires Azure AD Premium P2)
az rest --method GET --url "https://graph.microsoft.com/v1.0/me" --query displayName
```

---

## PIM — Privileged Identity Management

PIM uses the Microsoft Graph API via `az rest`. The az CLI doesn't have native PIM commands.

### List Your Eligible Roles

```bash
# Get your principal ID
MY_ID=$(az ad signed-in-user show --query id -o tsv)

# List eligible role assignments
az rest --method GET \
  --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleEligibilityScheduleInstances?\$filter=principalId eq '$MY_ID'" \
  --query "value[].{role: roleDefinition.displayName, scope: directoryScopeId, id: roleDefinitionId}" \
  -o table
```

### List Eligible Azure Resource Roles (Subscriptions/Resource Groups)

```bash
SUB_ID=$(az account show --query id -o tsv)

# Eligible assignments for Azure resources (not directory roles)
az rest --method GET \
  --url "https://management.azure.com/subscriptions/$SUB_ID/providers/Microsoft.Authorization/roleEligibilityScheduleInstances?\api-version=2020-10-01" \
  --query "value[].{role: properties.expandedProperties.roleDefinition.displayName, scope: properties.expandedProperties.scope.id, principalName: properties.expandedProperties.principal.displayName}" \
  -o table
```

### Activate a PIM Role (Directory)

```bash
MY_ID=$(az ad signed-in-user show --query id -o tsv)

# Activate — replace ROLE_DEFINITION_ID with the id from the eligible list
az rest --method POST \
  --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignmentScheduleRequests" \
  --headers "Content-Type=application/json" \
  --body '{
    "principalId": "'$MY_ID'",
    "roleDefinitionId": "<ROLE_DEFINITION_ID>",
    "directoryScopeId": "/",
    "action": "selfActivate",
    "justification": "Operational task — <reason>",
    "scheduleInfo": {
      "startDateTime": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
      "expiration": {
        "type": "afterDuration",
        "duration": "PT8H"
      }
    }
  }'
```

### Activate a PIM Role (Azure Resource)

```bash
SUB_ID=$(az account show --query id -o tsv)
MY_ID=$(az ad signed-in-user show --query id -o tsv)

# Get the eligible assignment schedule instance ID first
ELIGIBLE_ID=$(az rest --method GET \
  --url "https://management.azure.com/subscriptions/$SUB_ID/providers/Microsoft.Authorization/roleEligibilityScheduleInstances?api-version=2020-10-01&\$filter=principalId eq '$MY_ID'" \
  --query "value[0].properties.roleEligibilityScheduleId" -o tsv)

az rest --method PUT \
  --url "https://management.azure.com/subscriptions/$SUB_ID/providers/Microsoft.Authorization/roleAssignmentScheduleRequests/$(uuidgen)?api-version=2020-10-01" \
  --headers "Content-Type=application/json" \
  --body '{
    "properties": {
      "principalId": "'$MY_ID'",
      "roleDefinitionId": "/subscriptions/'$SUB_ID'/providers/Microsoft.Authorization/roleDefinitions/<ROLE_DEF_ID>",
      "requestType": "SelfActivate",
      "linkedRoleEligibilityScheduleId": "'$ELIGIBLE_ID'",
      "justification": "Operational task — <reason>",
      "scheduleInfo": {
        "startDateTime": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
        "expiration": {
          "type": "AfterDuration",
          "duration": "PT8H"
        }
      }
    }
  }'
```

### Deactivate a PIM Role

```bash
az rest --method POST \
  --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignmentScheduleRequests" \
  --headers "Content-Type=application/json" \
  --body '{
    "principalId": "'$MY_ID'",
    "roleDefinitionId": "<ROLE_DEFINITION_ID>",
    "directoryScopeId": "/",
    "action": "selfDeactivate"
  }'
```

### Check Active Role Assignments

```bash
# Directory roles currently active
az rest --method GET \
  --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignmentScheduleInstances?\$filter=principalId eq '$MY_ID'" \
  --query "value[].{role: roleDefinition.displayName, startTime: startDateTime, endTime: endDateTime}" \
  -o table
```

---

## Support Tickets

### List Open Tickets

```bash
az support in-subscription tickets list --query "[?status=='Open'].{name: supportTicketId, title: title, severity: severity, status: status, created: createdDate}" -o table
```

### Create a Support Ticket

```bash
# Step 1: Find the service and problem category
az support services list -o table
az support services problem-classifications list --service-name "<service-name>" -o table

# Step 2: Create the ticket
az support in-subscription tickets create \
  --ticket-name "ticket-$(date +%Y%m%d%H%M%S)" \
  --title "Brief description of the issue" \
  --description "Detailed description including:\n- What happened\n- When it started\n- Impact\n- Steps to reproduce" \
  --severity "minimal" \
  --problem-classification "/providers/Microsoft.Support/services/<service-id>/problemClassifications/<classification-id>" \
  --contact-first-name "<first>" \
  --contact-last-name "<last>" \
  --contact-method "email" \
  --contact-email "<email>" \
  --contact-timezone "America/Chicago" \
  --contact-language "en-us" \
  --contact-country "US"
```

### Update a Ticket

```bash
# Add communication
az support in-subscription communication create \
  --ticket-name "<ticket-id>" \
  --communication-name "update-$(date +%s)" \
  --communication-body "Additional information or follow-up" \
  --communication-subject "Update: <topic>"

# Change severity
az support in-subscription tickets update \
  --ticket-name "<ticket-id>" \
  --severity "moderate"
```

### Check Ticket Status

```bash
az support in-subscription tickets show --ticket-name "<ticket-id>" \
  --query "{status: status, severity: severity, title: title, created: createdDate, modified: modifiedDate}" \
  -o jsonc
```

---

## Resource Locks

Prevent accidental deletion or modification of critical resources.

### List Locks

```bash
# All locks in subscription
az lock list -o table

# Locks on a specific resource group
az lock list --resource-group <rg-name> -o table
```

### Create a Lock

```bash
# Prevent deletion
az lock create --name "no-delete" --lock-type CanNotDelete \
  --resource-group <rg-name> \
  --notes "Protected — requires lock removal before deletion"

# Prevent any modification
az lock create --name "read-only" --lock-type ReadOnly \
  --resource-group <rg-name>
```

### Remove a Lock (when you need to make changes)

```bash
az lock delete --name "no-delete" --resource-group <rg-name>
```

---

## Cost Management

### Current Month Spend

```bash
az consumption usage list \
  --start-date $(date -v-30d +%Y-%m-%d) \
  --end-date $(date +%Y-%m-%d) \
  --query "[].{resource: instanceName, cost: pretaxCost, currency: currency}" \
  -o table
```

### Budget Status

```bash
az consumption budget list --query "[].{name: name, amount: amount, currentSpend: currentSpend.amount, timeGrain: timeGrain}" -o table
```

### Cost by Resource Group

```bash
# Requires Cost Management API
az rest --method POST \
  --url "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/providers/Microsoft.CostManagement/query?api-version=2023-03-01" \
  --headers "Content-Type=application/json" \
  --body '{
    "type": "ActualCost",
    "timeframe": "MonthToDate",
    "dataset": {
      "granularity": "None",
      "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
      "grouping": [{"type": "Dimension", "name": "ResourceGroupName"}]
    }
  }'
```

---

## Policy Compliance

### Check Compliance State

```bash
# Overall compliance summary
az policy state summarize --query "value[].{policy: policyDefinitionName, compliant: results.resourceDetails[0].count, nonCompliant: results.nonCompliantResources}" -o table

# Non-compliant resources
az policy state list --filter "complianceState eq 'NonCompliant'" \
  --query "[].{resource: resourceId, policy: policyDefinitionName, reason: complianceState}" \
  -o table
```

### Trigger Compliance Scan

```bash
# Trigger evaluation for a resource group
az policy state trigger-scan --resource-group <rg-name> --no-wait
```

---

## Quick Reference

| Task | Command |
|------|---------|
| List eligible PIM roles | `az rest --method GET --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleEligibilityScheduleInstances?..."` |
| Activate PIM role | `az rest --method POST --url ".../roleAssignmentScheduleRequests" --body '{...selfActivate...}'` |
| Open support ticket | `az support in-subscription tickets create --title "..." --severity "..." ...` |
| List open tickets | `az support in-subscription tickets list` |
| Create resource lock | `az lock create --name "..." --lock-type CanNotDelete --resource-group <rg>` |
| Check costs | `az consumption usage list --start-date ... --end-date ...` |
| Policy compliance | `az policy state summarize` |
