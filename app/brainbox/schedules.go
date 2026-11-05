package brainbox

import (
	"net/url"
)

// Schedule mirrors the server's event_schedules row (phantom-events
// scheduler.EventSchedule, proxied by phantom-router at /api/schedules).
//
// This is the server-side replacement for a user-created `collect_jobs` row.
// The app keeps MANAGING schedules — this client is how — but no longer runs
// them: the bus owns the clock and the router executes the actions, so a
// schedule fires with this app closed.
//
// Cadence lives in Schedule (a JSON object discriminated on "kind") rather
// than in flat interval/run_at/days columns the way the local table had it.
// The four kinds cover every cadence the local scheduler supported, plus cron
// which it never did:
//
//	{"kind":"interval","every_s":300}
//	{"kind":"daily","at":"08:30","days":"daily"|"weekdays"}
//	{"kind":"cron","expr":"0 */4 * * *"}
//	{"kind":"at","fire_at_ms":1730000000000}   // one-shot, self-disabling
//
// map[string]interface{} (not json.RawMessage) so Wails generates
// Record<string, any> bindings, matching how Rule.Pattern is handled.
type Schedule struct {
	ID          string                   `json:"id"`
	Name        string                   `json:"name"`
	Profile     string                   `json:"profile"` // "" or "global" = all workspaces
	Enabled     bool                     `json:"enabled"`
	Description string                   `json:"description"` // server null → ""
	Schedule    map[string]interface{}   `json:"schedule"`
	Timezone    string                   `json:"timezone"`
	Actions     []map[string]interface{} `json:"actions"`
	CatchUp     string                   `json:"catch_up"` // skip | run_once
	// Server-managed. NextRunAt is materialized, not derived, so a value in
	// the past means a genuinely missed window rather than "compute it".
	NextRunAt    *int64 `json:"next_run_at"`
	LastRunAt    *int64 `json:"last_run_at"`
	LastStatus   string `json:"last_status"` // fired | skipped; server null → ""
	TriggerCount int64  `json:"trigger_count"`
	CreatedAt    int64  `json:"created_at"` // epoch ms
	UpdatedAt    int64  `json:"updated_at"`
}

type ScheduleEnabledState struct {
	ID      string `json:"id"`
	Enabled bool   `json:"enabled"`
}

// ScheduleRunResult is the run-now reply. It reports how many actions were
// queued, not whether they succeeded — execution is asynchronous through the
// rules executions ledger.
type ScheduleRunResult struct {
	ScheduleID string `json:"schedule_id"`
	Queued     int    `json:"queued"`
}

// SchedulerStatus is the server loop's own health, for showing the operator
// that the clock is actually running (and its lag) rather than inferring it
// from whether rows changed.
type SchedulerStatus struct {
	Enabled       bool    `json:"enabled"`
	PollIntervalS float64 `json:"poll_interval_s"`
	BatchLimit    int     `json:"batch_limit"`
	MisfireGraceS int     `json:"misfire_grace_s"`
	LastRunMs     *int64  `json:"last_run_ms"`
	LastFired     int     `json:"last_fired"`
	TotalFired    int     `json:"total_fired"`
	LastError     string  `json:"last_error"` // server null → ""
}

// scheduleBody is the create/update payload — only operator-authored fields.
// The run statistics (trigger_count, last_run_at, last_status) and next_run_at
// are server-managed; sending them back would clobber the clock's own state.
type scheduleBody struct {
	Name        string                   `json:"name"`
	Profile     string                   `json:"profile"`
	Enabled     bool                     `json:"enabled"`
	Description string                   `json:"description"`
	Schedule    map[string]interface{}   `json:"schedule"`
	Timezone    string                   `json:"timezone"`
	Actions     []map[string]interface{} `json:"actions"`
	CatchUp     string                   `json:"catch_up"`
}

func scheduleToBody(s Schedule) scheduleBody {
	tz := s.Timezone
	if tz == "" {
		// The server defaults to UTC, which is wrong for a "run at 08:30"
		// schedule authored by a human looking at a local clock. Send the
		// machine's zone so a time-of-day cadence means what the operator saw.
		tz = localTimezone()
	}
	catchUp := s.CatchUp
	if catchUp == "" {
		catchUp = "skip"
	}
	return scheduleBody{
		Name:        s.Name,
		Profile:     s.Profile,
		Enabled:     s.Enabled,
		Description: s.Description,
		Schedule:    s.Schedule,
		Timezone:    tz,
		Actions:     s.Actions,
		CatchUp:     catchUp,
	}
}

// ListSchedules returns server-side schedules. A non-empty profile returns
// that profile's schedules plus global ones; empty returns all (the param is
// omitted entirely — the server treats absent as "no filter").
func (c *Client) ListSchedules(profile string) ([]Schedule, error) {
	path := "/api/schedules"
	if profile != "" {
		path += "?profile=" + url.QueryEscape(profile)
	}
	var resp struct {
		Schedules []Schedule `json:"schedules"`
	}
	if err := c.get(path, &resp); err != nil {
		return nil, err
	}
	return resp.Schedules, nil
}

func (c *Client) GetSchedule(id string) (Schedule, error) {
	var s Schedule
	err := c.get("/api/schedules/"+url.PathEscape(id), &s)
	return s, err
}

func (c *Client) CreateSchedule(s Schedule) (Schedule, error) {
	var created Schedule
	err := c.post("/api/schedules", scheduleToBody(s), &created)
	return created, err
}

func (c *Client) UpdateSchedule(s Schedule) (Schedule, error) {
	var updated Schedule
	err := c.put("/api/schedules/"+url.PathEscape(s.ID), scheduleToBody(s), &updated)
	return updated, err
}

func (c *Client) DeleteSchedule(id string) error {
	return c.delete("/api/schedules/"+url.PathEscape(id), nil)
}

// SetScheduleEnabled toggles the clock via the dedicated endpoints rather than
// a full-object save. A save would round-trip the server-managed run stats and
// clobber them; this also lets the server re-seed next_run_at on re-enable so
// a schedule disabled for a week does not come back instantly "missed".
func (c *Client) SetScheduleEnabled(id string, enabled bool) (ScheduleEnabledState, error) {
	verb := "disable"
	if enabled {
		verb = "enable"
	}
	var state ScheduleEnabledState
	err := c.post("/api/schedules/"+url.PathEscape(id)+"/"+verb, nil, &state)
	return state, err
}

// RunScheduleNow fires a schedule's actions immediately WITHOUT consuming its
// next scheduled slot — the server guarantees that, so the operator's play
// button cannot silently cancel the next real run.
func (c *Client) RunScheduleNow(id string) (ScheduleRunResult, error) {
	var res ScheduleRunResult
	err := c.post("/api/schedules/"+url.PathEscape(id)+"/run", nil, &res)
	return res, err
}

func (c *Client) GetSchedulerStatus() (SchedulerStatus, error) {
	var status SchedulerStatus
	err := c.get("/api/scheduler/status", &status)
	return status, err
}
