package doctor

import (
	"encoding/json"
	"io"
)

// Symbols for the fleet report. Reuses the local report's vocabulary so a
// reader who knows one knows the other.
const (
	symFleetPass   = symOK
	symFleetBroken = symFail
	symFleetSkip   = symSkip
)

// FormatFleetText renders a fleet report for humans: one block per credential
// showing both sides of the differential, then the verdict.
//
// Both statuses are always printed, even on a pass. The value of this report
// is the COMPARISON, and a line that showed only the conclusion would hide the
// evidence that makes the conclusion trustworthy.
func FormatFleetText(w io.Writer, r FleetReport, color bool) {
	c := palette(color)

	writef(w, "%s=== %s (fleet credential delivery) ===%s\n", c.blue, r.Profile, c.reset)
	if r.Runner != "" {
		writef(w, "  runner: %s\n", r.Runner)
	}
	writef(w, "  %s\n", FleetCoverageNote)

	for _, res := range r.Results {
		sym, col := fleetSymbolFor(res.Verdict, c)
		writef(w, "\n    %s%s%s %s\n", col, sym, c.reset, res.Credential)
		writef(w, "        local:   %s\n", res.Local)
		writef(w, "        fleet:   %s\n", res.Fleet)
		writef(w, "        verdict: %s%s%s", col, res.Verdict, c.reset)
		if res.Detail != "" {
			writef(w, " — %s", res.Detail)
		}
		writef(w, "\n")
		if res.Failed() && res.Fix != "" {
			writef(w, "        %s-> fix: %s%s\n", c.yellow, res.Fix, c.reset)
		}
	}

	writef(w, "\n")
	if failCount := r.FailCount(); failCount > 0 {
		writef(w, "%s%d credential(s) failed the delivery check%s\n", c.red, failCount, c.reset)
	} else {
		writef(w, "%sCredential delivery verified%s\n", c.green, c.reset)
	}
}

// FormatFleetJSON renders a fleet report as machine-readable JSON.
func FormatFleetJSON(w io.Writer, r FleetReport) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	if r.Results == nil {
		r.Results = []FleetResult{}
	}
	// The coverage caveat travels with the JSON too: a consumer that only
	// reads the machine output must not mistake a clean report for full
	// credential coverage.
	return enc.Encode(struct {
		FleetReport
		Coverage string `json:"coverage"`
	}{FleetReport: r, Coverage: FleetCoverageNote})
}

func fleetSymbolFor(v Verdict, c colors) (string, string) {
	switch v {
	case VerdictPass:
		return symFleetPass, c.green
	case VerdictDeliveryBroken, VerdictLocalFirst:
		return symFleetBroken, c.red
	default:
		return symFleetSkip, c.yellow
	}
}
