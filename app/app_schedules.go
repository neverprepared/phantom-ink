package main

import "phantom-ink/brainbox"

// Wails-bound surface for server-side schedules (phantom-events owns the clock
// and the schedule store; phantom-router proxies /api/schedules).
//
// This is the management half of retiring the app-local collect scheduler. The
// app still authors, edits, enables and hand-runs scheduled work — it just no
// longer executes it, so a schedule fires with this app closed, and two running
// instances cannot double-fire one job.
//
// All methods delegate to brainbox.Client; errors pass through so the panel can
// parse "HTTP 400: {detail: ...}" bodies for inline display the way the Rules
// tab already does.

func (a *App) ListSchedules(profile string) ([]brainbox.Schedule, error) {
	return a.client.ListSchedules(profile)
}

func (a *App) GetSchedule(id string) (brainbox.Schedule, error) {
	return a.client.GetSchedule(id)
}

// SaveSchedule creates (empty ID) or updates (ID set). Updates preserve the
// server-managed run statistics and, unless the cadence itself changed, the
// pending next_run_at — so renaming a schedule neither resets its clock nor
// makes it look like it has never run.
func (a *App) SaveSchedule(schedule brainbox.Schedule) (brainbox.Schedule, error) {
	if schedule.ID == "" {
		return a.client.CreateSchedule(schedule)
	}
	return a.client.UpdateSchedule(schedule)
}

func (a *App) DeleteSchedule(id string) error {
	return a.client.DeleteSchedule(id)
}

// SetScheduleEnabled uses the dedicated enable/disable endpoints rather than a
// full-object save, which would clobber server-managed stats.
func (a *App) SetScheduleEnabled(id string, enabled bool) (brainbox.ScheduleEnabledState, error) {
	return a.client.SetScheduleEnabled(id, enabled)
}

// RunScheduleNow is the panel's play button. It does NOT consume the next
// scheduled slot, so testing a schedule cannot silently cancel its next run.
func (a *App) RunScheduleNow(id string) (brainbox.ScheduleRunResult, error) {
	return a.client.RunScheduleNow(id)
}

func (a *App) GetSchedulerStatus() (brainbox.SchedulerStatus, error) {
	return a.client.GetSchedulerStatus()
}
