package doctor

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func sampleReport() Report {
	return Report{Profiles: []ProfileReport{{
		Profile: "demo",
		Path:    "/profiles/demo",
		Results: []Result{
			{Name: "profile env file", Category: CatStructure, Status: StatusOK, Detail: ".env present"},
			{Name: "expected env keys", Category: CatEnv, Status: StatusFail, Detail: "missing: CL_API_KEY", Fix: "add CL_API_KEY to .env"},
			{Name: "brain daemon", Category: CatBrain, Status: StatusSkip, Detail: "brain daemon at http://127.0.0.1:9998 unreachable — start it"},
		},
	}}}
}

func TestFormatText(t *testing.T) {
	var buf bytes.Buffer
	FormatText(&buf, sampleReport(), false)
	out := buf.String()

	for _, want := range []string{
		"=== demo ===",
		symOK + " profile env file",
		symFail + " expected env keys",
		symSkip + " brain daemon",
		"-> fix: add CL_API_KEY to .env",
		"1 ok, 1 failed, 1 skipped",
		"1 check(s) failed",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q\n---\n%s", want, out)
		}
	}
}

func TestFormatText_NoColorWhenDisabled(t *testing.T) {
	var buf bytes.Buffer
	FormatText(&buf, sampleReport(), false)
	if strings.Contains(buf.String(), "\033[") {
		t.Error("color escapes present with color disabled")
	}

	var colored bytes.Buffer
	FormatText(&colored, sampleReport(), true)
	if !strings.Contains(colored.String(), "\033[") {
		t.Error("expected color escapes with color enabled")
	}
}

func TestFormatText_AllPassed(t *testing.T) {
	r := Report{Profiles: []ProfileReport{{
		Profile: "demo",
		Results: []Result{{Name: "x", Category: CatStructure, Status: StatusOK}},
	}}}
	var buf bytes.Buffer
	FormatText(&buf, r, false)
	if !strings.Contains(buf.String(), "All checks passed") {
		t.Errorf("expected a success line, got:\n%s", buf.String())
	}
}

func TestFormatJSON(t *testing.T) {
	var buf bytes.Buffer
	if err := FormatJSON(&buf, sampleReport()); err != nil {
		t.Fatalf("FormatJSON() error: %v", err)
	}

	var decoded Report
	if err := json.Unmarshal(buf.Bytes(), &decoded); err != nil {
		t.Fatalf("output is not valid JSON: %v", err)
	}
	if len(decoded.Profiles) != 1 || len(decoded.Profiles[0].Results) != 3 {
		t.Fatalf("round-trip lost data: %+v", decoded)
	}
	if decoded.Profiles[0].Results[1].Status != StatusFail {
		t.Errorf("status did not round-trip: %v", decoded.Profiles[0].Results[1].Status)
	}
	if !decoded.Failed() {
		t.Error("decoded report should report a failure")
	}
}
