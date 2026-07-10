package main

// ---------------------------------------------------------------------------
// Profile images
// ---------------------------------------------------------------------------

// ProfileImageRow stores registry metadata for a built profile image.
type ProfileImageRow struct {
	Profile      string `json:"profile"`
	RegistryURL  string `json:"registry_url"`
	LastPushedAt string `json:"last_pushed_at"`
	LastDigest   string `json:"last_digest"`
	EnvKey       string `json:"env_key,omitempty"` // AES key for decrypting .env.enc at runtime
}

// UpsertProfileImage inserts or replaces a profile image record.
func (db *DB) UpsertProfileImage(r ProfileImageRow) error {
	_, err := db.conn.Exec(
		`INSERT INTO profile_images (profile, registry_url, last_pushed_at, last_digest, env_key)
		 VALUES (?, ?, ?, ?, ?)
		 ON CONFLICT(profile) DO UPDATE SET
		   registry_url   = excluded.registry_url,
		   last_pushed_at = excluded.last_pushed_at,
		   last_digest    = excluded.last_digest,
		   env_key        = excluded.env_key`,
		r.Profile, r.RegistryURL, r.LastPushedAt, r.LastDigest, r.EnvKey,
	)
	return err
}

// GetProfileImage returns the image record for a profile, if present.
func (db *DB) GetProfileImage(profile string) (ProfileImageRow, bool) {
	var r ProfileImageRow
	err := db.conn.QueryRow(
		`SELECT profile, registry_url, last_pushed_at, last_digest, env_key FROM profile_images WHERE profile = ?`,
		profile,
	).Scan(&r.Profile, &r.RegistryURL, &r.LastPushedAt, &r.LastDigest, &r.EnvKey)
	if err != nil {
		return ProfileImageRow{}, false
	}
	return r, true
}

// ListProfileImages returns all profile image records.
func (db *DB) ListProfileImages() []ProfileImageRow {
	rows, err := db.conn.Query(
		`SELECT profile, registry_url, last_pushed_at, last_digest, env_key FROM profile_images ORDER BY profile`,
	)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []ProfileImageRow
	for rows.Next() {
		var r ProfileImageRow
		if err := rows.Scan(&r.Profile, &r.RegistryURL, &r.LastPushedAt, &r.LastDigest, &r.EnvKey); err != nil {
			continue
		}
		out = append(out, r)
	}
	return out
}

// DeleteProfileImage removes a profile image record.
func (db *DB) DeleteProfileImage(profile string) error {
	_, err := db.conn.Exec("DELETE FROM profile_images WHERE profile = ?", profile)
	return err
}

// ---------------------------------------------------------------------------
