package jira

import (
	"reflect"
	"testing"
)

func TestParseKeys(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want []string
	}{
		{"simple", "ABC-123: add webhook", []string{"ABC-123"}},
		{"branch", "feature/ABC-123-add-webhook", []string{"ABC-123"}},
		{"multiple", "ABC-123 and DEF-9 both", []string{"ABC-123", "DEF-9"}},
		{"dedupe keeps first order", "DEF-9 then ABC-1 then DEF-9", []string{"DEF-9", "ABC-1"}},
		{"digits in project key", "AB2C-7 ships", []string{"AB2C-7"}},
		{"none", "bump deps", nil},
		{"lowercase is not a key", "abc-123 nope", nil},
		{"no leading digit project", "1AB-2 nope", nil},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ParseKeys(tt.in)
			if !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("ParseKeys(%q) = %v, want %v", tt.in, got, tt.want)
			}
		})
	}
}

func TestParseKeysMatchesKnownFalsePositives(t *testing.T) {
	// The regex DOES match these; FilterKeys is what removes them. Locking the
	// behaviour in so nobody "fixes" the regex and breaks real keys instead.
	for _, s := range []string{"UTF-8", "SHA-256", "ADR-003", "CVE-2024-1234", "PR-1"} {
		if got := ParseKeys(s); len(got) == 0 {
			t.Fatalf("ParseKeys(%q) = empty; expected the regex to match (filtering is FilterKeys' job)", s)
		}
	}
}

func TestFilterKeys(t *testing.T) {
	known := map[string]bool{"ABC": true, "DEF": true}
	got := FilterKeys([]string{"ABC-123", "UTF-8", "DEF-9", "CVE-2024-1234"}, known)
	want := []string{"ABC-123", "DEF-9"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("FilterKeys = %v, want %v", got, want)
	}
}

func TestFilterKeysFailsClosed(t *testing.T) {
	if got := FilterKeys([]string{"ABC-123"}, nil); got != nil {
		t.Fatalf("FilterKeys with nil known = %v, want nil (fail closed)", got)
	}
}
