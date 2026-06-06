# Morning Standup Summary (lakeview)

Profile-scoped daily standup for the lakeview workspace. Fetches all In Progress Jira tickets
assigned to the current user, reads yesterday's standup for context, and produces a formatted
daily summary with a Slack-ready copy/paste block.

- [ ] Use the Jira MCP tool to fetch all In Progress tickets assigned to me across all projects at https://lakeviewmsr.atlassian.net — collect Key, Summary, Status, and the date of the most recent comment or status change for each ticket; flag any ticket with no update in the last 48 hours as stale
- [ ] Read the file at $WORKSPACE_HOME/obsidian/vaults/lakeview-memory/Library/Daily Standups/$(date -v-1d +%Y-%m-%d).md if it exists — extract the Today section to use as additional context for Yesterday bullets; if the file does not exist skip this step
- [ ] Analyse the ticket list and yesterday's standup context: identify what moved forward yesterday, what is planned for today (max 3 items), and any blockers; note stale tickets (48h+ without update)
- [ ] Write a standup document with: a YAML frontmatter block (date, profile: lakeview, tags: [standup, jira]), a markdown pipe table with columns Key (linked to https://lakeviewmsr.atlassian.net/browse/KEY), Summary, Status — then ## Yesterday, ## Today (max 3 bullets), ## Blockers, and ## Slack Format sections; the Slack Format section must be a fenced code block containing plain-text Yesterday and Today bullet lists suitable for pasting into a Slack standup thread
- [ ] Store the standup summary in long-term memory using the phantom-brain memory tools with tags standup and lakeview
- [ ] Write the document to $WORKSPACE_HOME/obsidian/vaults/lakeview-memory/Library/Daily Standups/$(date +%Y-%m-%d).md — create the directory if it does not exist using mkdir -p
- [ ] Open the newly created standup note in Obsidian using: open "obsidian://open?vault=lakeview-memory&file=Library%2FDaily%20Standups%2F$(date +%Y-%m-%d)"
