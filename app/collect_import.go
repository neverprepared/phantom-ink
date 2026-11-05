package main

import (
	"fmt"

	"phantom-ink/brainbox"
)

// Carrying existing local collect jobs over to the server.
//
// The cutover makes the local scheduler widget-only, which means every
// user-created (source="") job in an existing database goes inert the moment
// the new build runs. Without this they would simply stop firing, silently, and
// the only symptom would be work that never happens.
//
// Three properties the import has to have, because an operator will press the
// button twice and will press it while the bus is flaky:
//
//   - idempotent — a row that has been carried over records the server id it
//     went to (collect_jobs.imported_schedule_id) and is skipped next time;
//   - non-destructive — the local row is marked, never deleted, so the import
//     is reversible and the old definition remains readable;
//   - partial-failure tolerant — one job the server rejects does not abort the
//     batch, and leaves its row unmarked so a retry picks it up.

// ScheduleImportNote explains why one job was not imported, in terms the
// operator can act on.
type ScheduleImportNote struct {
	JobID  string `json:"job_id"`
	Name   string `json:"name"`
	Reason string `json:"reason"`
}

// ScheduleImportResult is deliberately four separate buckets rather than a
// count. "7 of 9 imported" is not actionable; knowing WHICH two, and why, is.
type ScheduleImportResult struct {
	Imported        []string             `json:"imported"`         // local job ids
	AlreadyImported []string             `json:"already_imported"` // seen on a re-run
	Skipped         []ScheduleImportNote `json:"skipped"`          // cannot be represented server-side
	Failed          []ScheduleImportNote `json:"failed"`           // server rejected or was unreachable
}

// scheduleCreator is the seam the App wires to brainbox.Client.CreateSchedule.
// Taking it as a parameter keeps the conversion logic a pure function of the
// database, which is what makes the cadence mapping testable without a server.
type scheduleCreator func(brainbox.Schedule) (brainbox.Schedule, error)

// cadenceForJob maps a local job's cadence columns onto the server's schedule
// object. Precedence MUST match collectJobIsDue (one-shot, then time-of-day,
// then interval) or an imported schedule fires on a different clock than the
// job did locally. Returns nil when the job has no usable cadence at all.
func cadenceForJob(job CollectJob) map[string]interface{} {
	if job.RunOnceAtMs != nil {
		return map[string]interface{}{"kind": "at", "fire_at_ms": *job.RunOnceAtMs}
	}
	if job.RunAt != "" {
		days := job.Days
		if days == "" {
			days = "daily"
		}
		return map[string]interface{}{"kind": "daily", "at": job.RunAt, "days": days}
	}
	if job.IntervalS > 0 {
		return map[string]interface{}{"kind": "interval", "every_s": job.IntervalS}
	}
	return nil
}

// actionForJob maps a local job's target onto a server rule action. The second
// return is a reason when the job cannot be represented.
func actionForJob(job CollectJob) (map[string]interface{}, string) {
	switch job.TargetType {
	case "", "shell":
		if job.Command == "" {
			return nil, "the job has no command to run"
		}
		return map[string]interface{}{
			"type":              "run_profile_command",
			"command":           job.Command,
			"workspace_profile": job.Profile,
			// It was a collector, so its stdout entries must keep flowing —
			// server-side that means publishing them onto the bus.
			"emit_entries": true,
		}, ""
	case "runner":
		// Locally this shelled out to `claude -p <prompt>`. The server
		// equivalent is submit_task, which needs an agent identity the local
		// row never stored (target_id held a runner, not an agent name).
		// Guessing one would dispatch work to the wrong agent silently, so
		// report it and let the operator recreate it where the agent is chosen.
		return nil, "runner-target jobs need an agent name; recreate it from the Todos panel"
	default:
		// "loop" was declared in the local type but never had a dispatch case,
		// so such a job has never actually run as a loop.
		return nil, "unsupported target type " + job.TargetType
	}
}

// importLocalSchedules copies user-created local jobs to the server. An empty
// profile imports every profile's jobs.
func importLocalSchedules(db *DB, profile string, create scheduleCreator) ScheduleImportResult {
	res := ScheduleImportResult{
		Imported:        []string{},
		AlreadyImported: []string{},
		Skipped:         []ScheduleImportNote{},
		Failed:          []ScheduleImportNote{},
	}
	if db == nil {
		return res
	}
	jobs, err := db.ListCollectJobs(profile)
	if err != nil {
		return res
	}

	for _, job := range jobs {
		// Widget jobs are not candidates at all — they stay local by design,
		// so they are not even reported as skipped.
		if collectSchedulerOwns(job) {
			continue
		}
		if job.ImportedScheduleID != "" {
			res.AlreadyImported = append(res.AlreadyImported, job.ID)
			continue
		}

		cadence := cadenceForJob(job)
		if cadence == nil {
			res.Skipped = append(res.Skipped, ScheduleImportNote{
				JobID: job.ID, Name: job.Name,
				Reason: "the job has no interval, time-of-day or one-shot cadence",
			})
			continue
		}
		action, reason := actionForJob(job)
		if action == nil {
			res.Skipped = append(res.Skipped, ScheduleImportNote{
				JobID: job.ID, Name: job.Name, Reason: reason,
			})
			continue
		}

		created, err := create(brainbox.Schedule{
			Name:    job.Name,
			Profile: job.Profile,
			// A disabled local job must not come back running.
			Enabled:     job.Enabled,
			Description: "imported from a local collect job",
			Schedule:    cadence,
			Actions:     []map[string]interface{}{action},
			// The local scheduler silently skipped missed windows (its
			// same-day guard), so "skip" preserves the behaviour people are
			// used to rather than firing a surprise backlog on first run.
			CatchUp: "skip",
		})
		if err != nil {
			// Leave the row unmarked so a retry picks it up, and keep going —
			// one rejected job must not strand the rest of the batch.
			res.Failed = append(res.Failed, ScheduleImportNote{
				JobID: job.ID, Name: job.Name, Reason: err.Error(),
			})
			continue
		}
		if err := db.MarkCollectJobImported(job.ID, created.ID); err != nil {
			// The server has it but we could not record that locally. Report it
			// rather than claiming success: a re-run would create a duplicate.
			res.Failed = append(res.Failed, ScheduleImportNote{
				JobID: job.ID, Name: job.Name,
				Reason: "created on the server as " + created.ID +
					" but the local row could not be marked: " + err.Error(),
			})
			continue
		}
		res.Imported = append(res.Imported, job.ID)
	}
	return res
}

// ImportLocalSchedules copies this app's user-created collect jobs to the
// server so they keep firing after the local scheduler went widget-only.
// Safe to run repeatedly; an empty profile imports every profile's jobs.
func (a *App) ImportLocalSchedules(profile string) (ScheduleImportResult, error) {
	if a.db == nil {
		return ScheduleImportResult{}, fmt.Errorf("db not ready")
	}
	return importLocalSchedules(a.db, profile, a.client.CreateSchedule), nil
}
