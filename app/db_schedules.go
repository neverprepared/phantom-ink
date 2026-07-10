package main

// Schedules (cron)
// ---------------------------------------------------------------------------

type ScheduleRow struct {
	ID               string `json:"id"`
	SequenceID       string `json:"loop_id"`
	CronExpr         string `json:"cron_expr"`
	Input            string `json:"input"`
	Cwd              string `json:"cwd"`
	Enabled          bool   `json:"enabled"`
	WorkspaceProfile string `json:"workspace_profile"`
	CreatedAt        string `json:"created_at"`
	UpdatedAt        string `json:"updated_at"`
	LastFiredAt      string `json:"last_fired_at"`
	// NextFireAt is a computed convenience populated by the bindings (not the
	// table). RFC3339 UTC. Empty when the cron expression is invalid or the
	// schedule is disabled.
	NextFireAt string `json:"next_fire_at"`
}

func (db *DB) UpsertSchedule(s ScheduleRow) error {
	_, err := db.conn.Exec(`
		INSERT INTO schedules (id, loop_id, cron_expr, input, cwd, enabled, workspace_profile, created_at, updated_at, last_fired_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			loop_id          = excluded.loop_id,
			cron_expr         = excluded.cron_expr,
			input             = excluded.input,
			cwd               = excluded.cwd,
			enabled           = excluded.enabled,
			workspace_profile = excluded.workspace_profile,
			updated_at        = excluded.updated_at`,
		s.ID, s.SequenceID, s.CronExpr, s.Input, s.Cwd, boolToInt(s.Enabled), s.WorkspaceProfile,
		s.CreatedAt, s.UpdatedAt, s.LastFiredAt)
	return err
}

const scheduleCols = `id, loop_id, cron_expr, input, cwd, enabled, workspace_profile, created_at, updated_at, last_fired_at`

func scanSchedule(s rowScanner) (ScheduleRow, error) {
	var r ScheduleRow
	var enabled int
	err := s.Scan(&r.ID, &r.SequenceID, &r.CronExpr, &r.Input, &r.Cwd, &enabled, &r.WorkspaceProfile,
		&r.CreatedAt, &r.UpdatedAt, &r.LastFiredAt)
	r.Enabled = enabled != 0
	return r, err
}

func (db *DB) GetSchedule(id string) (ScheduleRow, bool) {
	r, err := scanSchedule(db.conn.QueryRow(`SELECT `+scheduleCols+` FROM schedules WHERE id = ?`, id))
	if err != nil {
		return r, false
	}
	return r, true
}

func (db *DB) ListSchedules(loopID string) ([]ScheduleRow, error) {
	q := `SELECT ` + scheduleCols + ` FROM schedules`
	var args []any
	if loopID != "" {
		q += ` WHERE loop_id = ?`
		args = append(args, loopID)
	}
	q += ` ORDER BY created_at`
	rows, err := db.conn.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ScheduleRow
	for rows.Next() {
		r, err := scanSchedule(rows)
		if err != nil {
			continue
		}
		out = append(out, r)
	}
	return out, nil
}

func (db *DB) DeleteSchedule(id string) error {
	_, err := db.conn.Exec("DELETE FROM schedules WHERE id = ?", id)
	return err
}

func (db *DB) MarkScheduleFired(id, firedAt string) error {
	_, err := db.conn.Exec("UPDATE schedules SET last_fired_at = ? WHERE id = ?", firedAt, id)
	return err
}
