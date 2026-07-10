package main

// ---------------------------------------------------------------------------
// Credential-bundle sources (v25) — per-profile toggle + custom definitions
// ---------------------------------------------------------------------------

// BundleSourceRow is one per-profile credential-bundle source selection.
// Catalog rows use only Profile/Name/Enabled; custom rows also carry the
// operator-authored Definition JSON ({globs, audience, env_map}).
type BundleSourceRow struct {
	Profile    string `json:"profile"`
	Name       string `json:"name"`
	Kind       string `json:"kind"` // catalog | custom
	Enabled    bool   `json:"enabled"`
	Definition string `json:"definition"`
}

// GetBundleSources returns all bundle-source rows for a profile.
func (db *DB) GetBundleSources(profile string) []BundleSourceRow {
	rows, err := db.conn.Query(
		`SELECT profile, name, kind, enabled, definition
		 FROM profile_bundle_sources WHERE profile = ? ORDER BY name`,
		profile,
	)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []BundleSourceRow
	for rows.Next() {
		var r BundleSourceRow
		var enabled int
		if err := rows.Scan(&r.Profile, &r.Name, &r.Kind, &enabled, &r.Definition); err != nil {
			continue
		}
		r.Enabled = enabled != 0
		out = append(out, r)
	}
	return out
}

// UpsertBundleSource inserts or updates one bundle-source row.
func (db *DB) UpsertBundleSource(r BundleSourceRow) error {
	if r.Kind == "" {
		r.Kind = "catalog"
	}
	if r.Definition == "" {
		r.Definition = "{}"
	}
	_, err := db.conn.Exec(
		`INSERT INTO profile_bundle_sources (profile, name, kind, enabled, definition)
		 VALUES (?, ?, ?, ?, ?)
		 ON CONFLICT(profile, name) DO UPDATE SET
		   kind       = excluded.kind,
		   enabled    = excluded.enabled,
		   definition = excluded.definition`,
		r.Profile, r.Name, r.Kind, boolToInt(r.Enabled), r.Definition,
	)
	return err
}

// DeleteBundleSource removes one bundle-source row.
func (db *DB) DeleteBundleSource(profile, name string) error {
	_, err := db.conn.Exec(
		`DELETE FROM profile_bundle_sources WHERE profile = ? AND name = ?`,
		profile, name,
	)
	return err
}
