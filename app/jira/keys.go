// Package jira derives Jira issue links from text the Code panel already has
// (pull-request titles, branch names) and resolves them against the Jira REST
// API. Links are DERIVED, never stored: there is no index to drift.
package jira

import (
	"regexp"
	"strings"
)

// keyRe matches a Jira issue key: an uppercase project key (letter first, then
// letters/digits) then a dash then digits. It deliberately over-matches —
// UTF-8, SHA-256 and ADR-003 all satisfy it. FilterKeys, not this regex, is
// what removes them, because tightening the pattern would also drop real
// project keys.
var keyRe = regexp.MustCompile(`\b[A-Z][A-Z0-9]*-\d+\b`)

// ParseKeys returns the issue keys in text, deduped, in first-appearance order.
func ParseKeys(text string) []string {
	matches := keyRe.FindAllString(text, -1)
	if len(matches) == 0 {
		return nil
	}
	seen := make(map[string]bool, len(matches))
	out := make([]string, 0, len(matches))
	for _, m := range matches {
		if seen[m] {
			continue
		}
		seen[m] = true
		out = append(out, m)
	}
	return out
}

// FilterKeys drops keys whose project prefix is not a real Jira project.
// A nil or empty known set returns nil: without a verified project list we
// show nothing rather than a panel full of chips for issues that don't exist.
func FilterKeys(keys []string, known map[string]bool) []string {
	if len(known) == 0 {
		return nil
	}
	var out []string
	for _, k := range keys {
		if i := strings.LastIndex(k, "-"); i > 0 && known[k[:i]] {
			out = append(out, k)
		}
	}
	return out
}
