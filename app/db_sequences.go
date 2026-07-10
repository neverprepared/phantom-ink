package main

// ---------------------------------------------------------------------------
// Sequences
// ---------------------------------------------------------------------------

// SequenceRow is the persisted form of a loop definition. The runtime Sequence type
// (with structured Steps) lives in loops.go and serializes Steps to/from
// StepsJSON via encoding/json. OnSuccessJSON is the same idea for the
// declarative followups list — read+written wholesale with the loop.
type SequenceRow struct {
	ID               string `json:"id"`
	Name             string `json:"name"`
	Description      string `json:"description"`
	StepsJSON        string `json:"steps_json"`
	Cwd              string `json:"cwd"`
	OnSuccessJSON    string `json:"on_success_json"`
	FilesJSON        string `json:"files_json"`
	WorkspaceProfile string `json:"workspace_profile"`
	CreatedAt        string `json:"created_at"`
	UpdatedAt        string `json:"updated_at"`
}

func (db *DB) UpsertSequence(c SequenceRow) error {
	if c.OnSuccessJSON == "" {
		c.OnSuccessJSON = "[]"
	}
	if c.FilesJSON == "" {
		c.FilesJSON = "[]"
	}
	_, err := db.conn.Exec(`
		INSERT INTO loops (id, name, description, steps_json, cwd, on_success_json, files_json, workspace_profile, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			name              = excluded.name,
			description       = excluded.description,
			steps_json        = excluded.steps_json,
			cwd               = excluded.cwd,
			on_success_json   = excluded.on_success_json,
			files_json        = excluded.files_json,
			workspace_profile = excluded.workspace_profile,
			updated_at        = excluded.updated_at`,
		c.ID, c.Name, c.Description, c.StepsJSON, c.Cwd, c.OnSuccessJSON, c.FilesJSON, c.WorkspaceProfile, c.CreatedAt, c.UpdatedAt)
	return err
}

const sequenceCols = `id, name, description, steps_json, cwd, on_success_json, files_json, workspace_profile, created_at, updated_at`

func scanSequence(s rowScanner) (SequenceRow, error) {
	var r SequenceRow
	err := s.Scan(&r.ID, &r.Name, &r.Description, &r.StepsJSON, &r.Cwd, &r.OnSuccessJSON, &r.FilesJSON, &r.WorkspaceProfile, &r.CreatedAt, &r.UpdatedAt)
	return r, err
}

func (db *DB) GetSequence(id string) (SequenceRow, bool) {
	r, err := scanSequence(db.conn.QueryRow(`SELECT `+sequenceCols+` FROM loops WHERE id = ?`, id))
	if err != nil {
		return r, false
	}
	return r, true
}

// ListSequences returns loops visible for the given profile: profile-owned loops
// plus global loops (workspace_profile=""). Pass "" to return all loops.
func (db *DB) ListSequences(profile string) ([]SequenceRow, error) {
	q := `SELECT ` + sequenceCols + ` FROM loops`
	var args []any
	if profile != "" {
		q += ` WHERE workspace_profile = '' OR workspace_profile = ?`
		args = append(args, profile)
	}
	q += ` ORDER BY name`
	rows, err := db.conn.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []SequenceRow
	for rows.Next() {
		r, err := scanSequence(rows)
		if err != nil {
			continue
		}
		out = append(out, r)
	}
	return out, nil
}

func (db *DB) DeleteSequence(id string) error {
	_, err := db.conn.Exec("DELETE FROM loops WHERE id = ?", id)
	return err
}

// SequenceRunRow is the persisted form of a single loop execution.
type SequenceRunRow struct {
	ID         string `json:"id"`
	SequenceID string `json:"loop_id"`
	StartedAt  string `json:"started_at"`
	FinishedAt string `json:"finished_at"`
	Status     string `json:"status"`
	LogJSON    string `json:"log_json"`
}

func (db *DB) InsertSequenceRun(r SequenceRunRow) error {
	_, err := db.conn.Exec(`
		INSERT INTO loop_runs (id, loop_id, started_at, finished_at, status, log_json)
		VALUES (?, ?, ?, ?, ?, ?)`,
		r.ID, r.SequenceID, r.StartedAt, r.FinishedAt, r.Status, r.LogJSON)
	return err
}

func (db *DB) UpdateSequenceRun(id, finishedAt, status, logJSON string) error {
	_, err := db.conn.Exec(`
		UPDATE loop_runs SET finished_at = ?, status = ?, log_json = ? WHERE id = ?`,
		finishedAt, status, logJSON, id)
	return err
}

func (db *DB) ListSequenceRuns(loopID string, limit int) ([]SequenceRunRow, error) {
	if limit <= 0 {
		limit = 25
	}
	rows, err := db.conn.Query(`
		SELECT id, loop_id, started_at, finished_at, status, log_json
		FROM loop_runs WHERE loop_id = ?
		ORDER BY started_at DESC LIMIT ?`, loopID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []SequenceRunRow
	for rows.Next() {
		var r SequenceRunRow
		if err := rows.Scan(&r.ID, &r.SequenceID, &r.StartedAt, &r.FinishedAt, &r.Status, &r.LogJSON); err != nil {
			continue
		}
		out = append(out, r)
	}
	return out, nil
}
