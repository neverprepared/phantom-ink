package dora

import (
	"strconv"
	"strings"
)

// Revert detection: the failure signal.
//
// Both GitHub's "Revert" button and `git revert` write a commit whose subject
// is `Revert "<original subject>"`. That quoted string is the only link back to
// what broke, so parsing it and matching it to a deploy is how a revert becomes
// a measurable change failure with a time to restore.
//
// Matching is EXACT, never fuzzy. A wrong match attributes a failure to a
// change that did not cause it and invents a restore duration out of two
// unrelated timestamps; an unmatched revert merely loses its restore sample
// while still counting toward the failure rate. Missing the link is the
// cheaper error, so this errs toward ok=false.

const revertPrefix = `Revert "`

// ParseRevertSubject extracts the reverted commit's subject from a revert
// commit message. It reads only the FIRST line — `git revert` puts "This
// reverts commit <sha>." in the body, which is not a subject.
//
// ok is false for anything that is not a well-formed revert subject, including
// a missing closing quote and an empty quoted string: neither identifies a
// change, and guessing would fabricate a failure event.
func ParseRevertSubject(commitMessage string) (string, bool) {
	line := strings.TrimSpace(firstLine(commitMessage))
	if !strings.HasPrefix(line, revertPrefix) {
		return "", false
	}
	rest := line[len(revertPrefix):]
	end := strings.LastIndex(rest, `"`)
	if end <= 0 {
		// end < 0: unterminated. end == 0: `Revert ""`, an empty subject.
		return "", false
	}
	return rest[:end], true
}

// MatchRevert maps a reverted subject back to the PR number of the deploy it
// undid.
//
// An explicit trailing "(#N)" in the subject is authoritative: squash merges
// carry it, and it disambiguates two PRs that happen to share a title. When
// such a number names no deploy in the set, the result is UNMATCHED rather than
// a title fallback — falling back would attribute the failure to a different,
// same-titled change, and a revert of a deploy older than the stored window is
// the ordinary reason the number is unknown.
//
// Without an explicit number, the subject is compared to each deploy's title
// with any "(#N)" suffix trimmed from both sides, so a subject and the squash
// title it came from still line up.
func MatchRevert(subject string, deploys []DeployEvent) (int, bool) {
	subject = strings.TrimSpace(subject)
	if subject == "" {
		return 0, false
	}
	if n, ok := trailingPRNumber(subject); ok {
		for _, d := range deploys {
			if d.PRNumber == n {
				return n, true
			}
		}
		return 0, false
	}
	for _, d := range deploys {
		title := strings.TrimSpace(d.Title)
		if title == "" {
			continue
		}
		if stripPRSuffix(title) == stripPRSuffix(subject) {
			return d.PRNumber, true
		}
	}
	return 0, false
}

// trailingPRNumber reads the N out of a subject ending in "(#N)".
func trailingPRNumber(s string) (int, bool) {
	s = strings.TrimSpace(s)
	if !strings.HasSuffix(s, ")") {
		return 0, false
	}
	open := strings.LastIndex(s, "(#")
	if open < 0 || open+2 >= len(s)-1 {
		return 0, false
	}
	n, err := strconv.Atoi(s[open+2 : len(s)-1])
	if err != nil || n <= 0 {
		return 0, false
	}
	return n, true
}

// stripPRSuffix removes a trailing "(#N)" so a squash-merge title and a bare
// subject compare equal.
func stripPRSuffix(s string) string {
	s = strings.TrimSpace(s)
	if _, ok := trailingPRNumber(s); !ok {
		return s
	}
	return strings.TrimSpace(s[:strings.LastIndex(s, "(#")])
}

func firstLine(s string) string {
	if i := strings.IndexAny(s, "\r\n"); i >= 0 {
		return s[:i]
	}
	return s
}
