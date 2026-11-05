package brainbox

import "time"

// localTimezone returns an IANA zone name for this machine, or "UTC" when it
// cannot be determined.
//
// Needed because a time-of-day schedule is authored against a human's local
// clock. The server stores a zone name (not an offset) and resolves each fire
// in it, which is what keeps "08:30" meaning 08:30 across a DST boundary — an
// offset captured once would drift by an hour twice a year.
//
// time.Local.String() yields the IANA name on macOS/Linux ("America/New_York")
// but can be "Local" when TZ is unset in some environments; that is not a zone
// the server can load, so fall back to UTC rather than send something that
// would be rejected.
func localTimezone() string {
	name := time.Local.String()
	if name == "" || name == "Local" {
		return "UTC"
	}
	return name
}
