package main

import (
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// DORA delivery-metric events
// ---------------------------------------------------------------------------
//
// Two event tables plus a per-repo watermark. Merges into a repo's default
// branch are the deploy signal; `Revert "..."` commits on it are the failure
// signal. Persisting them as events (rather than recomputing from the provider
// on every page load) is what makes the 30/90-day toggle free, lets the
// dashboard render offline, and accumulates history past the provider's own
// pagination horizon.
//
// Every accessor takes profile FIRST and filters on it in the WHERE clause.
// There is no accessor that reads across profiles, and none should be added:
// one profile seeing another's delivery metrics is a leak, not a cosmetic bug.
//
// Timestamps are RFC3339 UTC strings, matching the rest of the store. They sort
// lexicographically in that form, so `merged_at >= ?` is a correct chronological
// filter without a date function.

// DeployEventRow is one merge into a repo's default branch — the deploy signal.
// CreatedAt is when the PR was opened, which is the lead-time clock start.
type DeployEventRow struct {
	Provider     string `json:"provider"`
	RepoFullName string `json:"repo_full_name"`
	PRNumber     int    `json:"pr_number"`
	Title        string `json:"title"`
	Author       string `json:"author"`
	CreatedAt    string `json:"created_at"`
	MergedAt     string `json:"merged_at"`
	MergeSHA     string `json:"merge_sha"`
	HTMLURL      string `json:"html_url"`
}

// FailureEventRow is one revert commit on a default branch — the failure
// signal. MatchedPRNumber is 0 when the revert's subject could not be traced
// back to a known deploy: such a revert still counts toward the change failure
// rate but cannot contribute a time-to-restore measurement.
type FailureEventRow struct {
	Provider        string `json:"provider"`
	RepoFullName    string `json:"repo_full_name"`
	RevertSHA       string `json:"revert_sha"`
	RevertedSubject string `json:"reverted_subject"`
	MatchedPRNumber int    `json:"matched_pr_number"`
	RevertedAt      string `json:"reverted_at"`
}

// UpsertDeployEvents stores one profile's deploy events. Idempotent by
// (provider, repo, PR number): a sync whose window overlaps the previous one
// re-offers rows that are already stored, and a duplicate would inflate
// deployment frequency, so the conflict is a no-op rather than a replace.
func (db *DB) UpsertDeployEvents(profile string, evs []DeployEventRow) error {
	profile = strings.TrimSpace(profile)
	if profile == "" {
		return errNoDB
	}
	if len(evs) == 0 {
		return nil
	}
	tx, err := db.conn.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stmt, err := tx.Prepare(
		`INSERT INTO dora_deploy_events
		   (profile, provider, repo_full_name, pr_number, title, author,
		    created_at, merged_at, merge_sha, html_url)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT(profile, provider, repo_full_name, pr_number) DO NOTHING`)
	if err != nil {
		return err
	}
	defer stmt.Close()
	for _, e := range evs {
		if _, err := stmt.Exec(profile, e.Provider, e.RepoFullName, e.PRNumber,
			e.Title, e.Author, e.CreatedAt, e.MergedAt, e.MergeSHA, e.HTMLURL); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// UpsertFailureEvents stores one profile's revert events, idempotent by revert
// SHA for the same reason as the deploys above.
func (db *DB) UpsertFailureEvents(profile string, evs []FailureEventRow) error {
	profile = strings.TrimSpace(profile)
	if profile == "" {
		return errNoDB
	}
	if len(evs) == 0 {
		return nil
	}
	tx, err := db.conn.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stmt, err := tx.Prepare(
		`INSERT INTO dora_failure_events
		   (profile, provider, repo_full_name, revert_sha, reverted_subject,
		    matched_pr_number, reverted_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT(profile, provider, repo_full_name, revert_sha) DO NOTHING`)
	if err != nil {
		return err
	}
	defer stmt.Close()
	for _, e := range evs {
		if _, err := stmt.Exec(profile, e.Provider, e.RepoFullName, e.RevertSHA,
			e.RevertedSubject, e.MatchedPRNumber, e.RevertedAt); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// DeployEventsSince returns one profile's deploy events merged at or after
// since, oldest first.
func (db *DB) DeployEventsSince(profile string, since time.Time) ([]DeployEventRow, error) {
	rows, err := db.conn.Query(
		`SELECT provider, repo_full_name, pr_number, title, author,
		        created_at, merged_at, merge_sha, html_url
		   FROM dora_deploy_events
		  WHERE profile = ? AND merged_at >= ?
		  ORDER BY merged_at`,
		strings.TrimSpace(profile), rfc3339UTC(since))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []DeployEventRow
	for rows.Next() {
		var e DeployEventRow
		if err := rows.Scan(&e.Provider, &e.RepoFullName, &e.PRNumber, &e.Title,
			&e.Author, &e.CreatedAt, &e.MergedAt, &e.MergeSHA, &e.HTMLURL); err != nil {
			continue
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// FailureEventsSince returns one profile's revert events at or after since,
// oldest first.
func (db *DB) FailureEventsSince(profile string, since time.Time) ([]FailureEventRow, error) {
	rows, err := db.conn.Query(
		`SELECT provider, repo_full_name, revert_sha, reverted_subject,
		        matched_pr_number, reverted_at
		   FROM dora_failure_events
		  WHERE profile = ? AND reverted_at >= ?
		  ORDER BY reverted_at`,
		strings.TrimSpace(profile), rfc3339UTC(since))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []FailureEventRow
	for rows.Next() {
		var e FailureEventRow
		if err := rows.Scan(&e.Provider, &e.RepoFullName, &e.RevertSHA,
			&e.RevertedSubject, &e.MatchedPRNumber, &e.RevertedAt); err != nil {
			continue
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// DORAWatermark returns how far a (profile, provider, repo) has been synced.
// A repo that has never been synced returns the ZERO time and a nil error:
// "never synced" is the normal first-run state, not a failure, and the caller
// turns a zero watermark into a full-history first fetch.
func (db *DB) DORAWatermark(profile, prov, repo string) (time.Time, error) {
	var raw string
	err := db.conn.QueryRow(
		`SELECT last_synced_at FROM dora_sync_state
		  WHERE profile = ? AND provider = ? AND repo_full_name = ?`,
		strings.TrimSpace(profile), prov, repo).Scan(&raw)
	if err != nil {
		// sql.ErrNoRows and an unreadable row alike mean "no usable
		// watermark" — re-fetching a window is cheap and idempotent, whereas
		// failing the sync would leave the dashboard permanently empty.
		return time.Time{}, nil
	}
	t, perr := time.Parse(time.RFC3339, raw)
	if perr != nil {
		return time.Time{}, nil
	}
	return t.UTC(), nil
}

// SetDORAWatermark advances a repo's watermark. Unlike the event upserts this
// one REPLACES on conflict: the watermark is a moving position, not an
// immutable fact, and a DO NOTHING here would pin the sync to its first run.
func (db *DB) SetDORAWatermark(profile, prov, repo string, at time.Time) error {
	profile = strings.TrimSpace(profile)
	if profile == "" {
		return errNoDB
	}
	_, err := db.conn.Exec(
		`INSERT INTO dora_sync_state (profile, provider, repo_full_name, last_synced_at)
		 VALUES (?, ?, ?, ?)
		 ON CONFLICT(profile, provider, repo_full_name)
		 DO UPDATE SET last_synced_at = excluded.last_synced_at`,
		profile, prov, repo, rfc3339UTC(at))
	return err
}

// rfc3339UTC renders a time the way every timestamp in this store is written,
// so string comparison in SQL stays chronological.
func rfc3339UTC(t time.Time) string { return t.UTC().Format(time.RFC3339) }
