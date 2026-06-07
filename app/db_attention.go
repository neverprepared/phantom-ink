package main

import "time"

// DismissAttentionRow persists a dismissal so the same item never reappears in
// the Stream panel's attention queue after the user clears it.
func (db *DB) DismissAttentionRow(id string) error {
	_, err := db.conn.Exec(
		`INSERT OR REPLACE INTO dismissed_attention (id, dismissed_at) VALUES (?, ?)`,
		id, time.Now().UnixMilli())
	return err
}

// UndismissAttentionRow removes a dismissal — used by the "show dismissed" toggle
// or an explicit restore action.
func (db *DB) UndismissAttentionRow(id string) error {
	_, err := db.conn.Exec(`DELETE FROM dismissed_attention WHERE id = ?`, id)
	return err
}

// DismissedAttentionSet returns the set of dismissed AttentionItem IDs.  Used
// by the attention aggregator to filter results before returning to the UI.
func (db *DB) DismissedAttentionSet() (map[string]bool, error) {
	rows, err := db.conn.Query(`SELECT id FROM dismissed_attention`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make(map[string]bool)
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			continue
		}
		out[id] = true
	}
	return out, nil
}

// SetAttentionReply stores the user's reply text for an envelope id (P5).
// Replaces attention_items.user_reply now that attention reads from the bus.
func (db *DB) SetAttentionReply(id, reply string) error {
	_, err := db.conn.Exec(
		`INSERT INTO attention_replies (id, reply, replied_at) VALUES (?, ?, ?)
		 ON CONFLICT(id) DO UPDATE SET reply = excluded.reply, replied_at = excluded.replied_at`,
		id, reply, time.Now().UnixMilli())
	return err
}

// AttentionReplies returns the map of envelope id → reply text. Used by the
// aggregator to overlay replies on bus-sourced rows.
func (db *DB) AttentionReplies() (map[string]string, error) {
	rows, err := db.conn.Query(`SELECT id, reply FROM attention_replies`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make(map[string]string)
	for rows.Next() {
		var id, reply string
		if err := rows.Scan(&id, &reply); err != nil {
			continue
		}
		out[id] = reply
	}
	return out, nil
}
