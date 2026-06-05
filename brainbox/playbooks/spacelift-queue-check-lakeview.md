# Spacelift Queue Check (lakeview)

Morning queue health check for the lakeview Spacelift account. Scans for stacks with
unconfirmed runs (waiting on manual approval) and checks whether the running queue backlog
is growing. Sends a Microsoft Teams ping when action is required.

Requires `LAKEVIEW_TEAMS_QUEUE_WEBHOOK` in the lakeview profile `.env` — set it to the
incoming webhook URL for the infrastructure or ops channel.

- [ ] Query the Spacelift GraphQL API for all runs currently in UNCONFIRMED state using:
  `spacectl api '{ runs(states: [UNCONFIRMED]) { id state createdAt stack { id name } } }'`
  Parse the JSON output. For each unconfirmed run calculate how long it has been waiting
  (now minus createdAt). A run waiting more than 2 hours is considered stuck.

- [ ] Query for all runs currently in RUNNING state using:
  `spacectl api '{ runs(states: [RUNNING]) { id state createdAt stack { id name } } }'`
  Count the total running runs and flag any individual run that has been in RUNNING state
  for more than 1 hour (potential hung run).

- [ ] Assess the queue health:
  - **Stuck unconfirmed**: any run waiting > 2 hours for approval
  - **Hung running**: any run in RUNNING state > 1 hour
  - **Backlog concern**: 5 or more runs in RUNNING state simultaneously
  - If none of these conditions are true, the queue is healthy — skip the Teams ping step
    and print a one-line summary to stdout instead

- [ ] If any health concern was found, build a Teams MessageCard payload and send it via:
  `curl -s -X POST "$LAKEVIEW_TEAMS_QUEUE_WEBHOOK" -H "Content-Type: application/json" -d '<payload>'`
  The card must include:
  - Title: "🚨 Spacelift Queue Attention Required" (or "⚠️ Spacelift Queue Warning" for backlog-only)
  - A facts section listing each problem: stack name, run ID, state, and how long it has been
    waiting — link each stack name to `https://lakeview.app.spacelift.io/stack/<stack-id>`
  - A single action button "Open Spacelift" linking to `https://lakeview.app.spacelift.io`
  - Use the Teams MessageCard schema (`@type: MessageCard`, `@context: http://schema.org/extensions`)

- [ ] Print a final summary to stdout: date/time, counts (unconfirmed total, running total,
  stuck count, hung count), and whether a Teams ping was sent
