package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// ContainerStat represents live resource usage for a Docker container.
type ContainerStat struct {
	Name     string `json:"name"`
	ID       string `json:"id"`
	CPUPerc  string `json:"cpu_perc"`
	MemUsage string `json:"mem_usage"`
	MemPerc  string `json:"mem_perc"`
	NetIO    string `json:"net_io"`
	BlockIO  string `json:"block_io"`
	PIDs     string `json:"pids"`
}

// GetDockerStats returns live resource stats for all running containers.
func (a *App) GetDockerStats() ([]ContainerStat, error) {
	cmd := exec.Command("docker", "stats", "--no-stream", "--format",
		`{"name":"{{.Name}}","id":"{{.ID}}","cpu_perc":"{{.CPUPerc}}","mem_usage":"{{.MemUsage}}","mem_perc":"{{.MemPerc}}","net_io":"{{.NetIO}}","block_io":"{{.BlockIO}}","pids":"{{.PIDs}}"}`)
	out, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	var stats []ContainerStat
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line == "" {
			continue
		}
		var s ContainerStat
		if err := json.Unmarshal([]byte(line), &s); err != nil {
			continue
		}
		stats = append(stats, s)
	}
	return stats, nil
}

// ContainerDiskStat represents disk usage for a single container.
type ContainerDiskStat struct {
	Name              string `json:"name"`
	WritableSize      string `json:"writable_size"`       // e.g. "125MB" — writable layer only
	WritableSizeBytes int64  `json:"writable_size_bytes"` // writable layer in bytes for graphing
	VirtualSize       string `json:"virtual_size"`        // e.g. "2.4GB" — image + writable
}

// parseDockerSize converts a Docker size string (e.g. "125MB", "2.4GB") to bytes.
// Docker uses SI units (powers of 1000).
func parseDockerSize(s string) int64 {
	s = strings.TrimSpace(s)
	if s == "" || s == "0B" {
		return 0
	}
	var num float64
	var unit string
	fmt.Sscanf(s, "%f%s", &num, &unit)
	switch strings.ToUpper(unit) {
	case "B":
		return int64(num)
	case "KB":
		return int64(num * 1_000)
	case "MB":
		return int64(num * 1_000_000)
	case "GB":
		return int64(num * 1_000_000_000)
	case "TB":
		return int64(num * 1_000_000_000_000)
	}
	return 0
}

// GetContainerDiskUsage returns disk usage for all containers (running and stopped).
// Uses `docker ps --size` which reports the writable layer size and total virtual size.
func (a *App) GetContainerDiskUsage() ([]ContainerDiskStat, error) {
	cmd := exec.Command("docker", "ps", "-a", "--size", "--format",
		`{"name":"{{.Names}}","size":"{{.Size}}"}`)
	out, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	var stats []ContainerDiskStat
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line == "" {
			continue
		}
		var raw struct {
			Name string `json:"name"`
			Size string `json:"size"`
		}
		if err := json.Unmarshal([]byte(line), &raw); err != nil {
			continue
		}
		// Size format: "125MB (virtual 2.4GB)" — split on " (virtual "
		writable, virtual := raw.Size, ""
		if idx := strings.Index(raw.Size, " (virtual "); idx != -1 {
			writable = raw.Size[:idx]
			virtual = strings.TrimSuffix(raw.Size[idx+10:], ")")
		}
		stats = append(stats, ContainerDiskStat{
			Name:              raw.Name,
			WritableSize:      writable,
			WritableSizeBytes: parseDockerSize(writable),
			VirtualSize:       virtual,
		})
	}
	return stats, nil
}

// DiskCategory represents disk usage for one category.
type DiskCategory struct {
	Name  string `json:"name"`
	Bytes int64  `json:"bytes"`
	Label string `json:"label"` // human-readable e.g. "1.2 GB"
}

// DiskBreakdown is the full disk usage summary.
type DiskBreakdown struct {
	Total      int64          `json:"total_bytes"`
	TotalLabel string         `json:"total_label"`
	Categories []DiskCategory `json:"categories"`
}

func humanBytes(b int64) string {
	switch {
	case b >= 1_000_000_000_000:
		return fmt.Sprintf("%.1f TB", float64(b)/1_000_000_000_000)
	case b >= 1_000_000_000:
		return fmt.Sprintf("%.1f GB", float64(b)/1_000_000_000)
	case b >= 1_000_000:
		return fmt.Sprintf("%.1f MB", float64(b)/1_000_000)
	case b >= 1_000:
		return fmt.Sprintf("%.1f KB", float64(b)/1_000)
	default:
		return fmt.Sprintf("%d B", b)
	}
}

// dirSize returns total size of all files under a directory.
func dirSize(path string) int64 {
	var total int64
	filepath.Walk(path, func(_ string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}
		total += info.Size()
		return nil
	})
	return total
}

// GetDiskBreakdown returns disk usage grouped by category:
// containers (writable layers), images, sessions data, workspace config.
func (a *App) GetDiskBreakdown() DiskBreakdown {
	var cats []DiskCategory

	// 1. Container writable layers
	var containerTotal int64
	if disks, err := a.GetContainerDiskUsage(); err == nil {
		for _, d := range disks {
			containerTotal += d.WritableSizeBytes
		}
	}
	cats = append(cats, DiskCategory{Name: "containers", Bytes: containerTotal, Label: humanBytes(containerTotal)})

	// 2. Docker images — parse `docker system df --format`
	var imageBytes int64
	if out, err := exec.Command("docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}").Output(); err == nil {
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			parts := strings.SplitN(line, "\t", 2)
			if len(parts) == 2 && parts[0] == "Images" {
				imageBytes = parseDockerSize(strings.TrimSpace(parts[1]))
			}
		}
	}
	cats = append(cats, DiskCategory{Name: "images", Bytes: imageBytes, Label: humanBytes(imageBytes)})

	// 3. Sessions data — brainbox sessions directory
	var sessionsBytes int64
	home, _ := os.UserHomeDir()
	configHome := os.Getenv("XDG_CONFIG_HOME")
	if configHome == "" {
		configHome = filepath.Join(home, ".config")
	}
	sessionsDir := filepath.Join(configHome, "phantom-ink", "brainbox", "sessions")
	sessionsBytes = dirSize(sessionsDir)
	cats = append(cats, DiskCategory{Name: "sessions", Bytes: sessionsBytes, Label: humanBytes(sessionsBytes)})

	// 4. Workspace config — .claude directory
	var configBytes int64
	claudeDir := os.Getenv("CLAUDE_CONFIG_DIR")
	if claudeDir == "" {
		claudeDir = filepath.Join(home, ".claude")
	}
	configBytes = dirSize(claudeDir)
	cats = append(cats, DiskCategory{Name: "config", Bytes: configBytes, Label: humanBytes(configBytes)})

	var total int64
	for _, c := range cats {
		total += c.Bytes
	}

	return DiskBreakdown{
		Total:      total,
		TotalLabel: humanBytes(total),
		Categories: cats,
	}
}

// ProfileDiskUsage represents disk usage for a single profile.
type ProfileDiskUsage struct {
	Name  string `json:"name"`
	Bytes int64  `json:"bytes"`
	Label string `json:"label"`
}

// DiskOverview is the full-disk pie chart data.
type DiskOverview struct {
	TotalDisk  int64              `json:"total_disk"`  // full disk capacity
	TotalLabel string             `json:"total_label"`
	UsedDisk   int64              `json:"used_disk"`
	UsedLabel  string             `json:"used_label"`
	Profiles   []ProfileDiskUsage `json:"profiles"`
	OSBytes    int64              `json:"os_bytes"` // everything not accounted for by profiles
	OSLabel    string             `json:"os_label"`
	ScannedAt  string             `json:"scanned_at"` // ISO 8601 timestamp of last scan, empty if no cache
}

// diskOverviewFromCache builds a DiskOverview using cached profile sizes.
func (a *App) diskOverviewFromCache() DiskOverview {
	overview := DiskOverview{}

	home, _ := os.UserHomeDir()
	var stat syscall.Statfs_t
	if err := syscall.Statfs(home, &stat); err == nil {
		overview.TotalDisk = int64(stat.Blocks) * int64(stat.Bsize)
		overview.TotalLabel = humanBytes(overview.TotalDisk)
		free := int64(stat.Bavail) * int64(stat.Bsize)
		overview.UsedDisk = overview.TotalDisk - free
		overview.UsedLabel = humanBytes(overview.UsedDisk)
	}

	// Read cached profile sizes
	var profileTotal int64
	var latestScan string
	if a.db != nil {
		rows, err := a.db.conn.Query("SELECT profile_name, bytes, scanned_at FROM disk_cache ORDER BY profile_name")
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var name string
				var bytes int64
				var scannedAt string
				if err := rows.Scan(&name, &bytes, &scannedAt); err == nil {
					overview.Profiles = append(overview.Profiles, ProfileDiskUsage{
						Name: name, Bytes: bytes, Label: humanBytes(bytes),
					})
					profileTotal += bytes
					if scannedAt > latestScan {
						latestScan = scannedAt
					}
				}
			}
		}
	}

	overview.ScannedAt = latestScan
	overview.OSBytes = overview.UsedDisk - profileTotal
	if overview.OSBytes < 0 {
		overview.OSBytes = 0
	}
	overview.OSLabel = humanBytes(overview.OSBytes)
	return overview
}

// GetDiskOverview returns cached disk overview (instant). Call ScanDiskUsage to refresh.
func (a *App) GetDiskOverview() DiskOverview {
	return a.diskOverviewFromCache()
}

// ScanDiskUsage walks each profile's workspace_home, updates the cache,
// and returns the fresh DiskOverview. This is slow — call only on user request.
func (a *App) ScanDiskUsage() DiskOverview {
	profiles, err := a.ScanProfiles()
	if err != nil {
		profiles = nil
	}

	now := time.Now().UTC().Format(time.RFC3339)

	for _, p := range profiles {
		if p.WorkspaceHome == "" {
			continue
		}
		bytes := dirSize(p.WorkspaceHome)
		if a.db != nil {
			if _, err := a.db.conn.Exec(
				"INSERT INTO disk_cache (profile_name, bytes, scanned_at) VALUES (?, ?, ?) "+
					"ON CONFLICT(profile_name) DO UPDATE SET bytes=excluded.bytes, scanned_at=excluded.scanned_at",
				p.Name, bytes, now,
			); err != nil {
				logErr("disk cache update for profile %q: %v", p.Name, err)
			}
		}
	}

	// Clean up profiles that no longer exist
	if a.db != nil {
		profileNames := make(map[string]bool)
		for _, p := range profiles {
			profileNames[p.Name] = true
		}
		rows, err := a.db.conn.Query("SELECT profile_name FROM disk_cache")
		if err != nil {
			logErr("disk cache cleanup query: %v", err)
		} else {
			defer rows.Close()
			var toDelete []string
			for rows.Next() {
				var name string
				if rows.Scan(&name) == nil && !profileNames[name] {
					toDelete = append(toDelete, name)
				}
			}
			for _, name := range toDelete {
				if _, err := a.db.conn.Exec("DELETE FROM disk_cache WHERE profile_name = ?", name); err != nil {
					logErr("disk cache purge %q: %v", name, err)
				}
			}
		}
	}

	return a.diskOverviewFromCache()
}

// LogEntry represents a single log line with metadata.
type LogEntry struct {
	Line string `json:"line"`
}

// GetAPILogs returns the last N lines from the brainbox daemon log file.
func (a *App) GetAPILogs(lines int) []LogEntry {
	if lines <= 0 {
		lines = 200
	}
	if lines > 2000 {
		lines = 2000
	}

	home, _ := os.UserHomeDir()
	configHome := os.Getenv("XDG_CONFIG_HOME")
	if configHome == "" {
		configHome = filepath.Join(home, ".config")
	}
	logPath := filepath.Join(configHome, "phantom-ink", "brainbox", "logs", "brainbox.log")

	data, err := os.ReadFile(logPath)
	if err != nil {
		return nil
	}

	allLines := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
	start := len(allLines) - lines
	if start < 0 {
		start = 0
	}

	var entries []LogEntry
	for _, l := range allLines[start:] {
		if l != "" {
			entries = append(entries, LogEntry{Line: l})
		}
	}
	return entries
}

// SystemInfo holds system-level CPU and memory totals.
type SystemInfo struct {
	CPUCores  int     `json:"cpu_cores"`
	MemTotalB int64   `json:"mem_total_bytes"`
	MemTotalG float64 `json:"mem_total_gib"`
}

// GetSystemInfo returns the host's CPU core count and total memory.
func (a *App) GetSystemInfo() SystemInfo {
	info := SystemInfo{}

	// CPU cores
	if out, err := exec.Command("sysctl", "-n", "hw.ncpu").Output(); err == nil {
		if n, err := strconv.Atoi(strings.TrimSpace(string(out))); err == nil {
			info.CPUCores = n
		}
	}
	// Fallback for Linux
	if info.CPUCores == 0 {
		if out, err := exec.Command("nproc").Output(); err == nil {
			if n, err := strconv.Atoi(strings.TrimSpace(string(out))); err == nil {
				info.CPUCores = n
			}
		}
	}

	// Total memory
	if out, err := exec.Command("sysctl", "-n", "hw.memsize").Output(); err == nil {
		if n, err := strconv.ParseInt(strings.TrimSpace(string(out)), 10, 64); err == nil {
			info.MemTotalB = n
			info.MemTotalG = float64(n) / (1024 * 1024 * 1024)
		}
	}
	// Fallback for Linux: /proc/meminfo
	if info.MemTotalB == 0 {
		if out, err := exec.Command("bash", "-c", `grep MemTotal /proc/meminfo | awk '{print $2}'`).Output(); err == nil {
			if kb, err := strconv.ParseInt(strings.TrimSpace(string(out)), 10, 64); err == nil {
				info.MemTotalB = kb * 1024
				info.MemTotalG = float64(info.MemTotalB) / (1024 * 1024 * 1024)
			}
		}
	}

	return info
}
