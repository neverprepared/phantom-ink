# Self-Improvement Ratchet

Scans lessons-learned from the second brain, identifies entries that can be fixed in code/prompts/config, and opens PRs for each fix.

- [ ] Search the second brain for all lessons-learned entries: `memory_search(tags=["lessons-learned", "self-correction"], para="areas", limit=50)`. List each entry with its title, problem summary, and whether it's marked "Fixable In Code: yes". Report the full list.
- [ ] For each lesson marked "Fixable In Code: yes" that has not been resolved, identify the specific file(s) and change(s) needed. Write a concrete fix plan as a numbered list. Skip lessons where the fix is unclear or requires human judgment.
- [ ] Clone the repo using `$BRAINBOX_REPO_URL`. For each fixable lesson, implement the fix on a unique branch (`fix/lesson-${BRAINBOX_TASK_ID:0:8}`). Commit with a clear message referencing the lesson title. Open a PR. Wait for CI green.
- [ ] For each lesson that was fixed, update the second brain entry to mark it resolved: `memory_update(id="<lesson_id>", content="<original content>\n\n## Resolution\nFixed in PR <url>\nStatus: resolved")`. Store a summary of all fixes made under `projects/self-improvement/<date>`.
