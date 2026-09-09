package main

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"phantom-ink/brainbox"
)

// Timeline — a single per-profile time axis that unifies the app's three
// scheduling sources (cron schedules, collect jobs, and schedule-once/repeatable
// todos, which are themselves collect jobs) on the "planned" side and the agent-
// event bus on the "executed" side. The frontend TimelinePanel renders planned
// items above a now-line and executed items below it.
//
// This file holds only the read/merge + one-shot reschedule logic. The planned
// side reuses cronParser (scheduler.go) and the collect-job due-logic parsing
// (collect.go); the executed side is a thin projection over SearchAgentEvents
// (app_bus.go) — no new network path.

const (
	// timelinePlannedCap bounds how many planned occurrences a single query can
	// return after merging all sources, so a tiny-interval job over a wide
	// window can't flood the panel.
	timelinePlannedCap = 500
	// timelineDefaultWindowFwd is how far ahead ListPlannedItems expands when the
	// caller passes untilMs <= 0.
	timelineDefaultWindowFwd = 7 * 24 * time.Hour
	// timelineMaxOccurrencesPerItem caps per-source expansion so one recurring
	// item can't dominate the merged list (and guards against runaway loops).
	timelineMaxOccurrencesPerItem = 200
)

// PlannedItem is one future occurrence of a schedule or collect job, expanded
// onto the wall clock. A recurring source yields several PlannedItems (one per
// occurrence inside the query window); a one-shot yields exactly one. Only
// one-shots are Draggable — recurring cadence is edited in its own panel.
type PlannedItem struct {
	ID         string `json:"id"`        // stable per occurrence: "<source>:<source_id>:<fire_at_ms>"
	Source     string `json:"source"`    // "schedule" | "collect"
	SourceID   string `json:"source_id"` // schedule.ID or collect job.ID (the mutate target)
	Title      string `json:"title"`
	Profile    string `json:"profile"`
	FireAtMs   int64  `json:"fire_at_ms"`
	Recurrence string `json:"recurrence"` // "once" | "interval" | "timeofday" | "cron"
	Draggable  bool   `json:"draggable"`  // true only for one-shot collect jobs
	Kind       string `json:"kind"`       // collect TargetType ("runner"|"loop"|"shell") or "loop" for schedules
}

// TimelineEvent is one executed item for the past side — a normalised
// projection of a top-level bus envelope so both axis sides share one shape.
type TimelineEvent struct {
	ID        string `json:"id"`
	Title     string `json:"title"`
	Type      string `json:"type"`
	Status    string `json:"status"`
	StartAtMs int64  `json:"start_at_ms"`
	EndAtMs   int64  `json:"end_at_ms"`
	SourceID  string `json:"source_id"` // parent envelope id if any, else ""
}

// ListPlannedItems returns the future fire-times of every schedule + collect
// job for a profile, expanded within [max(sinceMs, now), untilMs] and merged
// ascending by time. Empty profile spans all profiles. sinceMs<=0 defaults to
// now; untilMs<=0 defaults to now + 7 days.
func (a *App) ListPlannedItems(profile string, sinceMs, untilMs int64) ([]PlannedItem, error) {
	if err := a.requireDB(); err != nil {
		return nil, err
	}
	now := time.Now()
	if sinceMs <= 0 {
		sinceMs = now.UnixMilli()
	}
	if untilMs <= 0 {
		untilMs = now.Add(timelineDefaultWindowFwd).UnixMilli()
	}

	out := []PlannedItem{}

	// Cron schedules (filtered to the profile; empty profile = all).
	if schedules, err := a.db.ListSchedules(""); err == nil {
		loopNames := map[string]string{}
		if list, e := a.db.ListSequences(""); e == nil {
			for _, c := range list {
				loopNames[c.ID] = c.Name
			}
		}
		for _, s := range schedules {
			if profile != "" && s.WorkspaceProfile != profile {
				continue
			}
			out = append(out, expandSchedule(s, sinceMs, untilMs, now, loopNames)...)
		}
	}

	// Collect jobs (one-shot / time-of-day / interval). Timed todos live here too.
	if jobs, err := a.db.ListCollectJobs(profile); err == nil {
		for _, j := range jobs {
			out = append(out, expandCollectJob(j, sinceMs, untilMs, now)...)
		}
	}

	sort.Slice(out, func(i, j int) bool { return out[i].FireAtMs < out[j].FireAtMs })
	if len(out) > timelinePlannedCap {
		out = out[:timelinePlannedCap]
	}
	return out, nil
}

// RescheduleOneShot moves a one-shot collect job (incl. a schedule-once todo) to
// a new wall-clock time — the drag-drop target. It refuses a non-one-shot job
// (recurring cadence is edited in its own panel) and a job that already fired.
func (a *App) RescheduleOneShot(sourceID string, fireAtMs int64) (CollectJob, error) {
	if err := a.requireDB(); err != nil {
		return CollectJob{}, err
	}
	if fireAtMs <= 0 {
		return CollectJob{}, fmt.Errorf("fire_at_ms must be a positive epoch-ms value")
	}
	job, ok := a.db.GetCollectJob(sourceID)
	if !ok {
		return CollectJob{}, fmt.Errorf("collect job %q not found", sourceID)
	}
	if job.RunOnceAtMs == nil {
		return CollectJob{}, fmt.Errorf("job %q is not a one-shot; edit its cadence in the Collectors/Schedules panel", sourceID)
	}
	// A fired one-shot is normally deleted by the scheduler; if one lingers
	// (between mark-run and delete) refuse rather than silently no-op — Upsert
	// does not clear last_run_at, so it could never fire at the new time.
	if job.LastRunAt != nil {
		return CollectJob{}, fmt.Errorf("job %q already fired; create a new one-shot instead", sourceID)
	}
	ms := fireAtMs
	job.RunOnceAtMs = &ms
	if err := a.db.UpsertCollectJob(job); err != nil {
		return CollectJob{}, err
	}
	a.emitCollectUpdate(job.Profile)
	return job, nil
}

// ListExecutedItems projects the profile's recent bus history onto the timeline.
// It is a thin wrapper over SearchAgentEvents that keeps only top-level
// envelopes (task/loop/entry/action — not per-step noise) and de-dupes to one
// row per envelope id (search is newest-first). Returns an empty slice when
// brainbox is unreachable so the panel renders "no activity" without an error.
func (a *App) ListExecutedItems(profile string, sinceMs, untilMs int64, limit int) ([]TimelineEvent, error) {
	if a.client == nil {
		return []TimelineEvent{}, nil
	}
	if limit <= 0 {
		limit = 500
	}
	res, err := a.client.SearchAgentEvents(brainbox.SearchAgentEventsOptions{
		Workspace: profile,
		SinceMs:   sinceMs,
		UntilMs:   untilMs,
		Limit:     limit,
	})
	if err != nil {
		return nil, fmt.Errorf("search agent events: %w", err)
	}
	seen := map[string]bool{}
	out := []TimelineEvent{}
	for _, e := range res.Items {
		if e.ID == "" || seen[e.ID] || !isTopLevelTimelineID(e.ID) {
			continue
		}
		seen[e.ID] = true
		out = append(out, timelineEventFromEntry(e))
	}
	return out, nil
}

// ── expansion helpers ───────────────────────────────────────────────────────

// expandSchedule enumerates a cron schedule's fires within (start, untilMs],
// where start = max(now, sinceMs). Recurring → not draggable.
func expandSchedule(s ScheduleRow, sinceMs, untilMs int64, now time.Time, loopNames map[string]string) []PlannedItem {
	if !s.Enabled {
		return nil
	}
	sched, err := cronParser.Parse(s.CronExpr)
	if err != nil {
		return nil
	}
	start := now
	if sinceMs > start.UnixMilli() {
		start = time.UnixMilli(sinceMs)
	}
	title := scheduleTitle(s, loopNames)
	var out []PlannedItem
	t := sched.Next(start)
	for i := 0; i < timelineMaxOccurrencesPerItem && !t.IsZero() && t.UnixMilli() <= untilMs; i++ {
		out = append(out, PlannedItem{
			ID:         fmt.Sprintf("schedule:%s:%d", s.ID, t.UnixMilli()),
			Source:     "schedule",
			SourceID:   s.ID,
			Title:      title,
			Profile:    s.WorkspaceProfile,
			FireAtMs:   t.UnixMilli(),
			Recurrence: "cron",
			Draggable:  false,
			Kind:       "loop",
		})
		t = sched.Next(t)
	}
	return out
}

// expandCollectJob dispatches on the job's scheduling mode. One-shot → a single
// draggable point; time-of-day / interval → recurring read-only occurrences.
func expandCollectJob(j CollectJob, sinceMs, untilMs int64, now time.Time) []PlannedItem {
	if !j.Enabled {
		return nil
	}
	base := PlannedItem{
		Source:   "collect",
		SourceID: j.ID,
		Title:    j.Name,
		Profile:  j.Profile,
		Kind:     j.TargetType,
	}
	switch {
	case j.RunOnceAtMs != nil:
		if j.LastRunAt != nil { // already fired (awaiting delete) — not upcoming
			return nil
		}
		ms := *j.RunOnceAtMs
		if ms < sinceMs || ms > untilMs {
			return nil
		}
		it := base
		it.ID = fmt.Sprintf("collect:%s:%d", j.ID, ms)
		it.FireAtMs = ms
		it.Recurrence = "once"
		it.Draggable = true
		return []PlannedItem{it}
	case j.RunAt != "":
		return expandTimeOfDay(j, base, sinceMs, untilMs, now)
	case j.IntervalS > 0:
		return expandInterval(j, base, sinceMs, untilMs, now)
	default:
		return nil
	}
}

// expandInterval steps an interval job forward from its next fire (last_run +
// interval, or now if never run), aligned into the window.
func expandInterval(j CollectJob, base PlannedItem, sinceMs, untilMs int64, now time.Time) []PlannedItem {
	step := int64(j.IntervalS) * 1000
	if step <= 0 {
		return nil
	}
	nowMs := now.UnixMilli()
	next := nowMs
	if j.LastRunAt != nil {
		next = *j.LastRunAt + step
	}
	if next < nowMs { // overdue — it fires ~now; don't fabricate past points
		next = nowMs
	}
	if next < sinceMs { // fast-forward to the first occurrence inside the window
		k := (sinceMs - next) / step
		if (sinceMs-next)%step != 0 {
			k++
		}
		next += k * step
	}
	var out []PlannedItem
	for i := 0; i < timelineMaxOccurrencesPerItem && next <= untilMs; i++ {
		it := base
		it.ID = fmt.Sprintf("collect:%s:%d", j.ID, next)
		it.FireAtMs = next
		it.Recurrence = "interval"
		it.Draggable = false
		out = append(out, it)
		next += step
	}
	return out
}

// expandTimeOfDay enumerates "HH:MM" daily/weekdays occurrences within the
// window, mirroring the parsing in collectJobIsDue.
func expandTimeOfDay(j CollectJob, base PlannedItem, sinceMs, untilMs int64, now time.Time) []PlannedItem {
	var h, m int
	if _, err := fmt.Sscanf(j.RunAt, "%d:%d", &h, &m); err != nil {
		return nil
	}
	loc := now.Location()
	startMs := now.UnixMilli()
	if sinceMs > startMs {
		startMs = sinceMs
	}
	anchor := time.UnixMilli(startMs).In(loc)
	d := time.Date(anchor.Year(), anchor.Month(), anchor.Day(), h, m, 0, 0, loc)
	var out []PlannedItem
	for i := 0; i < timelineMaxOccurrencesPerItem; i++ {
		ms := d.UnixMilli()
		if ms > untilMs {
			break
		}
		if ms >= startMs {
			wd := d.Weekday()
			weekendSkip := j.Days == "weekdays" && (wd == time.Saturday || wd == time.Sunday)
			if !weekendSkip {
				it := base
				it.ID = fmt.Sprintf("collect:%s:%d", j.ID, ms)
				it.FireAtMs = ms
				it.Recurrence = "timeofday"
				it.Draggable = false
				out = append(out, it)
			}
		}
		d = d.AddDate(0, 0, 1)
	}
	return out
}

// ── misc helpers ────────────────────────────────────────────────────────────

func scheduleTitle(s ScheduleRow, loopNames map[string]string) string {
	if t := strings.TrimSpace(s.Input); t != "" {
		return t
	}
	if n := loopNames[s.SequenceID]; n != "" {
		return n
	}
	if s.SequenceID != "" {
		return s.SequenceID
	}
	return "schedule"
}

// isTopLevelTimelineID keeps envelope ids that represent a whole unit of work
// (task/loop/entry/action) and drops per-step noise ("loop-step:*"). Note
// "loop-step:" does not match the "loop:" prefix, so it is excluded.
func isTopLevelTimelineID(id string) bool {
	return strings.HasPrefix(id, "task:") ||
		strings.HasPrefix(id, "loop:") ||
		strings.HasPrefix(id, "entry:") ||
		strings.HasPrefix(id, "action:")
}

func timelineEventFromEntry(e brainbox.AgentEventEntry) TimelineEvent {
	start := e.Ts
	if v, ok := int64FromEnvelope(e.Envelope, "start_at"); ok {
		start = v
	}
	end := start
	if v, ok := int64FromEnvelope(e.Envelope, "end_at"); ok {
		end = v
	}
	return TimelineEvent{
		ID:        e.ID,
		Title:     stringFromEnvelope(e.Envelope, "title"),
		Type:      e.Type,
		Status:    e.Status,
		StartAtMs: start,
		EndAtMs:   end,
		SourceID:  e.ParentID,
	}
}

func stringFromEnvelope(m map[string]interface{}, key string) string {
	if m == nil {
		return ""
	}
	if v, ok := m[key].(string); ok {
		return v
	}
	return ""
}

func int64FromEnvelope(m map[string]interface{}, key string) (int64, bool) {
	if m == nil {
		return 0, false
	}
	switch v := m[key].(type) {
	case float64:
		return int64(v), true
	case int64:
		return v, true
	case int:
		return int64(v), true
	case json.Number:
		if n, err := v.Int64(); err == nil {
			return n, true
		}
	}
	return 0, false
}
