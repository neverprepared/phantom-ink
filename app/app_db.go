package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// DatabaseInfo is one per-service database on the platform Postgres.
type DatabaseInfo struct {
	Name string `json:"name"`
	Size string `json:"size"`
}

// platformPostgresContainer returns the id of the running phantom-platform
// postgres container, discovered by compose label (mirrors restartViaDocker).
func (a *App) platformPostgresContainer() (string, error) {
	out, err := exec.Command("docker", "ps",
		"--filter", "label=com.docker.compose.service=postgres",
		"--format", "{{.ID}} {{.Names}}").Output()
	if err != nil {
		return "", fmt.Errorf("docker ps: %w", err)
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) == 0 || lines[0] == "" {
		return "", fmt.Errorf("no platform postgres container is running")
	}
	// Prefer the phantom-platform one if several postgres containers exist.
	for _, ln := range lines {
		f := strings.Fields(ln)
		if len(f) >= 2 && strings.Contains(f[1], "phantom-platform") {
			return f[0], nil
		}
	}
	return strings.Fields(lines[0])[0], nil
}

// ListPlatformDatabases lists the per-service databases on the platform Postgres,
// excluding template/admin databases and throwaway _test databases.
func (a *App) ListPlatformDatabases() ([]DatabaseInfo, error) {
	cid, err := a.platformPostgresContainer()
	if err != nil {
		return nil, err
	}
	const q = "SELECT datname, pg_size_pretty(pg_database_size(datname)) " +
		"FROM pg_database WHERE datistemplate=false " +
		"AND datname NOT IN ('postgres','phantom') ORDER BY datname"
	out, err := exec.Command("docker", "exec", cid,
		"psql", "-U", "phantom", "-At", "-F", "|", "-c", q).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("list databases: %s", strings.TrimSpace(string(out)))
	}
	var dbs []DatabaseInfo
	for _, ln := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if ln == "" {
			continue
		}
		parts := strings.SplitN(ln, "|", 2)
		if strings.HasSuffix(parts[0], "_test") {
			continue
		}
		info := DatabaseInfo{Name: parts[0]}
		if len(parts) == 2 {
			info.Size = parts[1]
		}
		dbs = append(dbs, info)
	}
	return dbs, nil
}

// BackupDatabase dumps one database to a user-chosen file using pg_dump custom
// format (compressed and restorable via pg_restore). Returns the saved path.
func (a *App) BackupDatabase(db string) (string, error) {
	cid, err := a.platformPostgresContainer()
	if err != nil {
		return "", err
	}
	stamp := time.Now().Format("20060102-150405")
	dest, err := runtime.SaveFileDialog(a.ctx, runtime.SaveDialogOptions{
		Title:           "Save database backup",
		DefaultFilename: fmt.Sprintf("%s-%s.dump", db, stamp),
	})
	if err != nil || dest == "" {
		return "", err
	}
	f, err := os.Create(dest)
	if err != nil {
		return "", fmt.Errorf("create %s: %w", dest, err)
	}
	defer f.Close()

	cmd := exec.Command("docker", "exec", cid, "pg_dump", "-U", "phantom", "-Fc", db)
	cmd.Stdout = f
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		f.Close()
		os.Remove(dest)
		return "", fmt.Errorf("pg_dump: %s", strings.TrimSpace(stderr.String()))
	}
	return dest, nil
}

// RestoreDatabase restores one database from a user-chosen pg_dump file.
// DESTRUCTIVE: --clean --if-exists drops and recreates objects, so the frontend
// gates this behind an explicit confirmation. pg_restore commonly exits nonzero
// on ignorable warnings (--clean against a populated DB), so the raw output is
// surfaced rather than treated as a hard failure — the caller reviews it.
func (a *App) RestoreDatabase(db string) (string, error) {
	cid, err := a.platformPostgresContainer()
	if err != nil {
		return "", err
	}
	src, err := runtime.OpenFileDialog(a.ctx, runtime.OpenDialogOptions{
		Title: "Select a backup file to restore",
	})
	if err != nil || src == "" {
		return "", err
	}
	f, err := os.Open(src)
	if err != nil {
		return "", fmt.Errorf("open %s: %w", src, err)
	}
	defer f.Close()

	cmd := exec.Command("docker", "exec", "-i", cid,
		"pg_restore", "-U", "phantom", "--clean", "--if-exists", "--no-owner", "-d", db)
	cmd.Stdin = f
	var out strings.Builder
	cmd.Stdout = &out
	cmd.Stderr = &out
	runErr := cmd.Run()

	report := strings.TrimSpace(out.String())
	if len(report) > 4000 { // keep the return payload bounded
		report = "…" + report[len(report)-4000:]
	}
	status := "ok"
	if runErr != nil {
		status = fmt.Sprintf("finished with warnings (%v) — review output", runErr)
	}
	base := filepath.Base(src)
	if report == "" {
		return fmt.Sprintf("Restored %s from %s [%s].", db, base, status), nil
	}
	return fmt.Sprintf("Restored %s from %s [%s]:\n%s", db, base, status, report), nil
}
