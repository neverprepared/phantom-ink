# Second Brain Housekeeping

Reviews all memories in the Obsidian vault, identifies stale/misplaced/duplicate entries, and cleans up the PARA structure.

- [ ] Get an overview of the vault: `memory_stats()`. Then list all memories: `memory_list(limit=100)`. Report total count, breakdown by PARA category (projects/areas/resources/archives), and how many are stale (past TTL).
- [ ] Review `projects/` entries. Projects are time-bound — if the work is done (PR merged, ratchet completed, task finished), the entry should be archived. List each project entry and recommend: keep (still active), archive (done), or delete (empty/broken). For entries to archive, call `memory_archive(id="<id>")`.
- [ ] Review `areas/` entries. Areas are ongoing responsibilities — if an area entry is really a one-time finding or a reference doc, it belongs in `resources/`. If it's stale and no longer relevant, archive it. List each area entry and recommend: keep, move to resources (re-store with `para="resources"` then archive the old one), or archive.
- [ ] Review `resources/` entries. Resources are reference material — check for duplicates (similar titles/content), outdated information, or broken source URLs. List duplicates and recommend which to keep. Archive the duplicates via `memory_archive(id="<id>")`.
- [ ] Run cleanup for stale entries: `memory_cleanup(target="stale", action="list")`. Review the candidates — archive any that are genuinely stale and no longer useful. Skip any that are still relevant (update their TTL instead via `memory_update`). Report what was archived and what was kept.
- [ ] Store a housekeeping summary: `memory_store(title="housekeeping/<date>", content="## Summary\n- Reviewed: <n> memories\n- Archived: <n>\n- Moved: <n>\n- Kept: <n>\n\n### Actions taken\n- ...", para="resources", tags=["housekeeping", "maintenance"])`.
