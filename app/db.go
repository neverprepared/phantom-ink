package main

import (
	"database/sql"
	"fmt"
	_ "modernc.org/sqlite"
	"os"
	"path/filepath"
)

var dbPath = filepath.Join(os.Getenv("HOME"), ".config", "phantom-ink", "phantom-ink.db")

// DB wraps the SQLite connection.
type DB struct {
	conn *sql.DB
}

// Conn returns the underlying *sql.DB for packages that need direct access
// (e.g. internal/outbox, which manages its own table). Use sparingly.
func (db *DB) Conn() *sql.DB {
	return db.conn
}

// OpenDB opens (or creates) the phantom-ink database and runs migrations.
func OpenDB() (*DB, error) {
	if err := os.MkdirAll(filepath.Dir(dbPath), 0700); err != nil {
		return nil, fmt.Errorf("create config dir: %w", err)
	}

	conn, err := sql.Open("sqlite", dbPath+"?_pragma=journal_mode(wal)&_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, fmt.Errorf("open database: %w", err)
	}

	// One connection. Under WAL, concurrent writers serialise at the file
	// level and otherwise queue on busy_timeout(5000) -- and database/sql will
	// happily open a connection per concurrent caller, so the collect
	// scheduler, the outbox flusher, the env-refresh watcher and N bound UI
	// methods could pile up against each other until one hit a 5s SQLITE_BUSY
	// stall. That is not hypothetical here: the liveness-overlay goroutine in
	// app_agent_work.go exists to work around an observed hang attributed to
	// exactly this ("a contended pool stalls the whole method").
	//
	// Serialising in the pool turns that contention into a short queue instead
	// of a lock timeout. A single-file desktop SQLite app does not benefit from
	// a wider pool.
	conn.SetMaxOpenConns(1)
	conn.SetMaxIdleConns(1)
	conn.SetConnMaxLifetime(0)

	db := &DB{conn: conn}
	if err := db.migrate(); err != nil {
		conn.Close()
		return nil, fmt.Errorf("migrate database: %w", err)
	}
	return db, nil
}

// Close closes the database connection.
func (db *DB) Close() error {
	return db.conn.Close()
}

// rowScanner is satisfied by both *sql.Row and *sql.Rows, so one scan helper
// serves single-row (QueryRow) and multi-row (Query) call sites.
type rowScanner interface {
	Scan(dest ...any) error
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
