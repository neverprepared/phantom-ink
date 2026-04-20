package main

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
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
