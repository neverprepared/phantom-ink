package doctor

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func sampleFleetReport() FleetReport {
	return FleetReport{
		Profile: "demo",
		Runner:  "m3-64",
		Results: []FleetResult{{
			Credential: "GITHUB_TOKEN",
			Runner:     "m3-64",
			Local:      StatusOK,
			Fleet:      FleetRejected,
			Verdict:    VerdictDeliveryBroken,
			Detail:     "GitHub rejected the delivered token (401 Bad credentials)",
			Fix:        "re-curate GITHUB_TOKEN in the profile's gateway env store",
		}},
	}
}

// The report's value is the COMPARISON, so both sides must always be visible.
func TestFormatFleetText_ShowsBothSidesAndTheFix(t *testing.T) {
	var buf bytes.Buffer
	FormatFleetText(&buf, sampleFleetReport(), false)
	out := buf.String()

	for _, want := range []string{
		"GITHUB_TOKEN",
		"m3-64",
		"local:",
		string(StatusOK),
		"fleet:",
		string(FleetRejected),
		"verdict:",
		string(VerdictDeliveryBroken),
		"-> fix:",
		"re-curate",
		"1 credential(s) failed",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("text report is missing %q\n%s", want, out)
		}
	}
	// A clean run must not read as full credential coverage.
	if !strings.Contains(out, FleetCoverageNote) {
		t.Error("the text report must carry the coverage caveat")
	}
}

func TestFormatFleetText_PassingRun(t *testing.T) {
	r := sampleFleetReport()
	r.Results[0].Fleet = FleetAccepted
	r.Results[0].Verdict = VerdictPass
	r.Results[0].Fix = ""
	r.Results[0].Detail = "GITHUB_TOKEN delivered and accepted on m3-64"

	var buf bytes.Buffer
	FormatFleetText(&buf, r, false)
	out := buf.String()

	if !strings.Contains(out, "Credential delivery verified") {
		t.Errorf("a passing run must say so\n%s", out)
	}
	if strings.Contains(out, "-> fix:") {
		t.Errorf("a passing run must not print a fix hint\n%s", out)
	}
}

func TestFormatFleetJSON_CarriesTheCoverageCaveat(t *testing.T) {
	var buf bytes.Buffer
	if err := FormatFleetJSON(&buf, sampleFleetReport()); err != nil {
		t.Fatalf("FormatFleetJSON() error: %v", err)
	}

	var decoded struct {
		Profile  string        `json:"profile"`
		Runner   string        `json:"runner"`
		Results  []FleetResult `json:"results"`
		Coverage string        `json:"coverage"`
	}
	if err := json.Unmarshal(buf.Bytes(), &decoded); err != nil {
		t.Fatalf("output is not valid JSON: %v\n%s", err, buf.String())
	}
	if decoded.Profile != "demo" || decoded.Runner != "m3-64" {
		t.Errorf("identity = %q / %q", decoded.Profile, decoded.Runner)
	}
	if len(decoded.Results) != 1 {
		t.Fatalf("got %d results, want 1", len(decoded.Results))
	}
	got := decoded.Results[0]
	if got.Local != StatusOK || got.Fleet != FleetRejected || got.Verdict != VerdictDeliveryBroken {
		t.Errorf("row lost the differential: %+v", got)
	}
	// A consumer reading only JSON must not mistake a clean report for full
	// credential coverage.
	if decoded.Coverage == "" {
		t.Error("JSON output must carry the coverage caveat")
	}
}

// An empty result set must still emit a JSON array, not null.
func TestFormatFleetJSON_EmptyResults(t *testing.T) {
	var buf bytes.Buffer
	if err := FormatFleetJSON(&buf, FleetReport{Profile: "demo"}); err != nil {
		t.Fatalf("FormatFleetJSON() error: %v", err)
	}
	if !strings.Contains(buf.String(), `"results": []`) {
		t.Errorf("want an empty array, got:\n%s", buf.String())
	}
}
