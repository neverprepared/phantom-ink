package main

import (
	"database/sql"
	"encoding/json"
	"time"
)

// AttentionItemRow is the persisted form of a producer-driven attention item.
// Producers (queue worker, chain executor) insert rows directly; the aggregator
// unions them with the two legacy scraped sources.
type AttentionItemRow struct {
	ID          string   `json:"id"`
	Source      string   `json:"source"`    // "task" | "chain"
	SourceID    string   `json:"source_id"` // task id or chain run id
	Workspace   string   `json:"workspace"`
	Title       string   `json:"title"`
	Subtitle    string   `json:"subtitle"`
	Reason      string   `json:"reason"`
	URL         string   `json:"url"`
	Actions     []string `json:"actions"`      // ["retry","open","respond","dismiss"]
	ContextJSON string   `json:"context_json"` // producer-specific payload for retry
	UserReply   string   `json:"user_reply"`
	CreatedAt   int64    `json:"created_at"` // epoch ms
	ResolvedAt  *int64   `json:"resolved_at"`
}

// InsertAttentionItem upserts an attention item by id. Re-inserting an existing
// id resets resolved_at to NULL so a recurring failure resurfaces.
func (db *DB) InsertAttentionItem(item AttentionItemRow) error {
	actJSON, _ := json.Marshal(item.Actions)
	if item.ContextJSON == "" {
		item.ContextJSON = "{}"
	}
	_, err := db.conn.Exec(`
		INSERT INTO attention_items
			(id, source, source_id, workspace, title, subtitle, reason, url,
			 actions_json, context_json, user_reply, created_at, resolved_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
		ON CONFLICT(id) DO UPDATE SET
			title        = excluded.title,
			subtitle     = excluded.subtitle,
			reason       = excluded.reason,
			url          = excluded.url,
			actions_json = excluded.actions_json,
			context_json = excluded.context_json,
			created_at   = excluded.created_at,
			resolved_at  = NULL`,
		item.ID, item.Source, item.SourceID, item.Workspace,
		item.Title, item.Subtitle, item.Reason, item.URL,
		string(actJSON), item.ContextJSON, item.UserReply, item.CreatedAt)
	return err
}

// ListActiveAttention returns rows where resolved_at IS NULL, workspace-filtered.
// Pass "" for workspace to return items from all profiles.
func (db *DB) ListActiveAttention(workspace string) ([]AttentionItemRow, error) {
	var (
		rows *sql.Rows
		err  error
	)
	if workspace == "" {
		rows, err = db.conn.Query(`
			SELECT id, source, source_id, workspace, title, subtitle, reason, url,
			       actions_json, context_json, user_reply, created_at, resolved_at
			FROM attention_items
			WHERE resolved_at IS NULL
			ORDER BY created_at DESC`)
	} else {
		rows, err = db.conn.Query(`
			SELECT id, source, source_id, workspace, title, subtitle, reason, url,
			       actions_json, context_json, user_reply, created_at, resolved_at
			FROM attention_items
			WHERE resolved_at IS NULL AND workspace = ?
			ORDER BY created_at DESC`, workspace)
	}
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanAttentionItemRows(rows)
}

// ResolveAttentionItem marks the item resolved. Returns true if a row was found
// and updated, false if it was already resolved or not present.
func (db *DB) ResolveAttentionItem(id string) (bool, error) {
	res, err := db.conn.Exec(`
		UPDATE attention_items SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL`,
		time.Now().UnixMilli(), id)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// SetAttentionUserReply stores the user's reply text on the item. Does not
// auto-resolve — the user still dismisses explicitly when done.
func (db *DB) SetAttentionUserReply(id, text string) error {
	_, err := db.conn.Exec(`UPDATE attention_items SET user_reply = ? WHERE id = ?`, text, id)
	return err
}

// GetAttentionItem returns a single row by id.
func (db *DB) GetAttentionItem(id string) (AttentionItemRow, bool) {
	rows, err := db.conn.Query(`
		SELECT id, source, source_id, workspace, title, subtitle, reason, url,
		       actions_json, context_json, user_reply, created_at, resolved_at
		FROM attention_items WHERE id = ?`, id)
	if err != nil {
		return AttentionItemRow{}, false
	}
	defer rows.Close()
	items, err := scanAttentionItemRows(rows)
	if err != nil || len(items) == 0 {
		return AttentionItemRow{}, false
	}
	return items[0], true
}

func scanAttentionItemRows(rows *sql.Rows) ([]AttentionItemRow, error) {
	var out []AttentionItemRow
	for rows.Next() {
		var r AttentionItemRow
		var actJSON string
		var resolvedAt sql.NullInt64
		if err := rows.Scan(
			&r.ID, &r.Source, &r.SourceID, &r.Workspace,
			&r.Title, &r.Subtitle, &r.Reason, &r.URL,
			&actJSON, &r.ContextJSON, &r.UserReply, &r.CreatedAt, &resolvedAt,
		); err != nil {
			return nil, err
		}
		_ = json.Unmarshal([]byte(actJSON), &r.Actions)
		if resolvedAt.Valid {
			r.ResolvedAt = &resolvedAt.Int64
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
