# Queue Tasks — gsa

Manually-triggered playbook that reads today's Jira backlog and populates the day's work queue. Run this after the morning standup to turn the ticket list into actionable tasks.

- [ ] Use the Jira MCP tool to fetch all tickets assigned to me that are In Progress or To Do at $JIRA_URL — collect Key, Summary, Status, Priority, and any due dates
- [ ] Read today's standup document at $WORKSPACE_HOME/obsidian/vaults/gsa-memory/Library/Daily Standups/$(date +%Y-%m-%d).md if it exists — use the ## Today section as the priority signal for which tickets to schedule first
- [ ] Rank tickets: (1) anything flagged in today's standup, (2) In Progress tickets by most recent activity, (3) high-priority To Do tickets; exclude tickets with no activity in 7+ days unless they are explicitly in today's standup
- [ ] For each of the top 5 tickets, write a concise one-sentence action — what specifically needs to happen today on that ticket
- [ ] Write a task list document to $WORKSPACE_HOME/obsidian/vaults/gsa-memory/Library/Daily Tasks/$(date +%Y-%m-%d).md with YAML frontmatter (date, profile: gsa, tags: [tasks, jira]) and a markdown checklist of the ranked action items — create the directory if it does not exist using mkdir -p
- [ ] Open the task list in Obsidian using: open "obsidian://open?vault=gsa-memory&file=Library%2FDaily%20Tasks%2F$(date +%Y-%m-%d)"
