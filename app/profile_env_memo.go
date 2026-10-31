package main

import (
	"strings"
	"sync"
	"time"
)

// The per-profile gateway env is the app's most-repeated expensive read: one
// HTTP round trip to the broker, measured at a consistent 1.6s on this fleet,
// and every data path starts with it. A single Jira tab mount pays it three
// times concurrently -- JiraStatus, the ticket list, and the code fan-out --
// which is three times 1.6s for three identical answers.
//
// So it is memoised per profile, with two properties the existing profileMemo
// does not provide:
//
//   - single-flight on the COLD path. profileMemo coalesces only background
//     revalidation, but the case that costs here is several callers missing at
//     once on a panel mount. The first caller claims the fetch; the rest wait
//     on it and share its result.
//   - no serve-stale. A past-TTL env is a CREDENTIAL, and handing out a token
//     that may have been rotated is not the same kind of harmless as showing
//     slightly old pull requests.
//
// A short TTL rather than cache-until-invalidated is deliberate: ansible,
// pbrainctl and other nodes write this store too, and those writes are
// invisible to the app. Everything the app itself writes drops the entry
// explicitly -- see invalidateProfileEnv's callers.
const profileEnvTTL = 45 * time.Second

type envMemo struct {
	ttl time.Duration

	mu      sync.Mutex
	entries map[string]*envEntry
}

// envEntry is one profile's slot. `done` is closed when the fetch that owns
// this entry has settled, which is what later callers wait on instead of
// starting a second identical round trip.
type envEntry struct {
	done chan struct{}
	at   time.Time
	env  map[string]string
	err  error
}

func newEnvMemo(ttl time.Duration) *envMemo {
	return &envMemo{ttl: ttl, entries: map[string]*envEntry{}}
}

// envMemo hangs off the App rather than being a package global like
// codeOverviewMemo and jiraTicketsMemo. One App means one broker, so the
// lifetime is identical in production -- but the entries are keyed by profile
// alone, and a global would hand one test's fake broker's env to the next
// test's App. The existing globals are exactly why several tests have to call
// resetCodeOverviewCache() by hand; this one needs no such discipline.
//
// Lazily built so the zero App (`&App{}`, which every test uses) works.
func (a *App) envMemo() *envMemo {
	a.envMemoOnce.Do(func() { a.envMemoVal = newEnvMemo(profileEnvTTL) })
	return a.envMemoVal
}

// load returns profile's env, fetching it at most once across concurrent
// callers. The returned map is a copy: callers mutate what they are given
// (SetGatewayEnv deletes keys from it, refreshProfileEnv builds a write from
// it), and a shared map would let one of them corrupt every later read.
func (m *envMemo) load(profile string, fetch func(string) (map[string]string, error)) (map[string]string, error) {
	m.mu.Lock()
	if e, ok := m.entries[profile]; ok {
		select {
		case <-e.done:
			if e.err == nil && time.Since(e.at) < m.ttl {
				env := copyEnv(e.env)
				m.mu.Unlock()
				return env, nil
			}
			// Stale, or a cached failure that should not be: refetch below.
			delete(m.entries, profile)
		default:
			// A fetch is already in flight. Wait for it rather than firing a
			// second one -- this is the whole point on a panel mount.
			m.mu.Unlock()
			<-e.done
			// Safe to read without the lock: the owner writes these fields
			// before closing done.
			if e.err != nil {
				return nil, e.err
			}
			return copyEnv(e.env), nil
		}
	}
	e := &envEntry{done: make(chan struct{})}
	m.entries[profile] = e
	m.mu.Unlock()

	env, err := fetch(profile)

	m.mu.Lock()
	e.env, e.err, e.at = env, err, time.Now()
	if err != nil {
		// A failure is not memoised: the operator fixes a broker that was down
		// and expects the next read to try again.
		if m.entries[profile] == e {
			delete(m.entries, profile)
		}
	}
	m.mu.Unlock()
	close(e.done)

	if err != nil {
		return nil, err
	}
	return copyEnv(env), nil
}

// invalidate drops one profile's entry, or every profile's when profile is
// empty. An in-flight fetch is left alone: its waiters already have callers
// blocked on them, and the next read starts a fresh one.
func (m *envMemo) invalidate(profile string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if strings.TrimSpace(profile) == "" {
		m.entries = map[string]*envEntry{}
		return
	}
	delete(m.entries, profile)
}

// age backdates a settled entry, for tests that need one to read as stale.
func (m *envMemo) age(profile string, by time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if e, ok := m.entries[profile]; ok {
		e.at = e.at.Add(-by)
	}
}

func copyEnv(env map[string]string) map[string]string {
	out := make(map[string]string, len(env))
	for k, v := range env {
		out[k] = v
	}
	return out
}
