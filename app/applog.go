package main

import (
	"fmt"
	"os"
	"sync"
	"time"
)

// applog is an in-process ring buffer for operational errors.
// In a packaged macOS .app bundle stderr goes nowhere visible, so critical
// errors are written here and exposed via GetAppLogs() for in-app display.

const appLogMax = 200

var (
	appLogMu      sync.Mutex
	appLogEntries []string
)

// logErr writes a formatted message to both stderr and the in-process log.
func logErr(format string, args ...any) {
	msg := fmt.Sprintf(format, args...)
	ts := time.Now().Format("15:04:05")
	entry := fmt.Sprintf("[%s] %s", ts, msg)
	fmt.Fprintln(os.Stderr, entry)
	appLogMu.Lock()
	appLogEntries = append(appLogEntries, entry)
	if len(appLogEntries) > appLogMax {
		appLogEntries = appLogEntries[len(appLogEntries)-appLogMax:]
	}
	appLogMu.Unlock()
}

// GetAppLogs returns the buffered operational log entries, newest last.
// Called by the frontend to surface Go-side errors that would otherwise
// be invisible in a packaged .app bundle.
func (a *App) GetAppLogs() []string {
	appLogMu.Lock()
	defer appLogMu.Unlock()
	out := make([]string, len(appLogEntries))
	copy(out, appLogEntries)
	return out
}
