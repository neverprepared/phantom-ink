# Phantom Brain Maintenance

Daily maintenance pass for the phantom-brain second brain. Runs a full reflection cycle to detect orphans, re-score stale gates, clean broken provenance, prune done-queue files, and reap dead working-memory shards.

- [ ] Run `brain_reflect` with `scope: "full"` and report the summary — note how many orphans, stale entries, and done-queue files were handled
- [ ] Run `brain_synthesize` with `batch_size: 5` to drain any pending synthesis queue items; repeat until the queue is empty or you have processed at least 20 items total
- [ ] Report a one-paragraph summary of what was cleaned up and the current vault health
