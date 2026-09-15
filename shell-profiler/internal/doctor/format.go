package doctor

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"github.com/neverprepared/shell-profile-manager/internal/ui"
)

// p writes to w, discarding the write error: the report is advisory output and
// a broken pipe must not turn into a check failure.
func writef(w io.Writer, format string, a ...any) {
	_, _ = fmt.Fprintf(w, format, a...)
}

// Symbols used in the text report.
const (
	symOK   = "✓"
	symFail = "✗"
	symSkip = "→"
)

// FormatText renders a report for humans: grouped per profile, one line per
// check, with a `-> fix:` hint under every failure.
func FormatText(w io.Writer, r Report, color bool) {
	c := palette(color)
	for i, pr := range r.Profiles {
		if i > 0 {
			writef(w, "\n")
		}
		writef(w, "%s=== %s ===%s\n", c.blue, pr.Profile, c.reset)
		writef(w, "  %s\n", pr.Path)

		lastCategory := ""
		for _, res := range pr.Results {
			if res.Category != lastCategory {
				writef(w, "\n  %s%s%s\n", c.cyan, res.Category, c.reset)
				lastCategory = res.Category
			}
			sym, col := symbolFor(res.Status, c)
			writef(w, "    %s%s%s %s", col, sym, c.reset, res.Name)
			if res.Detail != "" {
				writef(w, ": %s", res.Detail)
			}
			writef(w, "\n")
			if res.Status == StatusFail && res.Fix != "" {
				writef(w, "        %s-> fix: %s%s\n", c.yellow, res.Fix, c.reset)
			}
		}

		okCount, failCount, skipCount := pr.Counts()
		writef(w, "\n  %d ok, %d failed, %d skipped\n", okCount, failCount, skipCount)
	}

	writef(w, "\n")
	if failCount := r.FailCount(); failCount > 0 {
		writef(w, "%s%d check(s) failed%s\n", c.red, failCount, c.reset)
	} else {
		writef(w, "%sAll checks passed%s\n", c.green, c.reset)
	}
}

// FormatJSON renders a report as machine-readable JSON.
func FormatJSON(w io.Writer, r Report) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	if r.Profiles == nil {
		r.Profiles = []ProfileReport{}
	}
	return enc.Encode(r)
}

type colors struct {
	reset, red, green, yellow, blue, cyan string
}

func palette(on bool) colors {
	if !on {
		return colors{}
	}
	return colors{
		reset:  ui.ColorReset,
		red:    ui.ColorRed,
		green:  ui.ColorGreen,
		yellow: ui.ColorYellow,
		blue:   ui.ColorBlue,
		cyan:   ui.ColorCyan,
	}
}

func symbolFor(s Status, c colors) (string, string) {
	switch s {
	case StatusOK:
		return symOK, c.green
	case StatusFail:
		return symFail, c.red
	default:
		return symSkip, c.yellow
	}
}

// Summary is a one-line description of a report, used by callers that want a
// terse result string.
func Summary(r Report) string {
	var parts []string
	for _, pr := range r.Profiles {
		okCount, failCount, skipCount := pr.Counts()
		parts = append(parts, fmt.Sprintf("%s: %d ok / %d fail / %d skip", pr.Profile, okCount, failCount, skipCount))
	}
	return strings.Join(parts, "; ")
}
