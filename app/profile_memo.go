package main

import (
	"strings"
	"sync"
	"time"
)

// profileMemo is a per-profile stale-while-revalidate memo for one expensive
// read.
//
// # Why this is shared
//
// The Code panel's launchpad and the Jira tab's ticket list have the same
// problem and the same shape: a provider fan-out, too slow to run on every
// panel mount, scoped per profile, and still worth DISPLAYING once past its
// TTL because it is what the operator was already looking at. Three pieces of
// that are subtle enough to be worth having exactly one copy of:
//
//   - serve-stale, so a past-TTL entry paints immediately instead of leaving
//     the panel empty behind a network round-trip;
//   - single-flight on both paths, so a panel mount landing while a refresh is
//     already in flight does not fan the whole provider set out twice -- and
//     neither do two cold callers arriving together, which is what the Jira
//     tab does on every mount (the code panel's own refresh plus the ticket
//     list's join, both reading the same overview);
//   - profile keying, which is what makes the cache safe at all. One profile
//     must never be served another's rows.
//
// # What it deliberately does not do
//
// Nothing is persisted: this is process-lifetime only, so a restart is a cold
// load. Nothing is evicted either -- the key space is one entry per profile
// the operator visits, which is a handful.
type profileMemo[T any] struct {
	ttl time.Duration

	mu      sync.Mutex
	entries map[string]memoEntry[T]
	// inflight tracks the one fetch allowed per profile, whether it was
	// started by a cold caller or by a background revalidate. Both kinds wait
	// on the same entry, so a cold miss landing during a revalidate joins it
	// instead of fanning out a second time.
	inflight map[string]*memoFlight[T]
}

// memoFlight is one in-progress fetch. `done` is closed once value/err are
// written, which is what later callers block on.
type memoFlight[T any] struct {
	done  chan struct{}
	value T
	err   error
}

type memoEntry[T any] struct {
	value T
	at    time.Time
}

func newProfileMemo[T any](ttl time.Duration) *profileMemo[T] {
	return &profileMemo[T]{
		ttl:      ttl,
		entries:  map[string]memoEntry[T]{},
		inflight: map[string]*memoFlight[T]{},
	}
}

// get returns profile's entry and whether it is still within the TTL.
//
// A stale entry is RETURNED, not withheld. ok=false means nothing is cached at
// all, which is the only case a caller must block on.
func (m *profileMemo[T]) get(profile string) (value T, fresh bool, ok bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	e, ok := m.entries[profile]
	if !ok {
		var zero T
		return zero, false, false
	}
	return e.value, time.Since(e.at) < m.ttl, true
}

// store memoises a value for profile, restarting its TTL.
func (m *profileMemo[T]) store(profile string, value T) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.entries[profile] = memoEntry[T]{value: value, at: time.Now()}
}

// invalidate drops one profile's entry, or every profile's when profile is
// empty. Used when the upstream data changed (credentials, a saved query) --
// NOT on a profile switch, which is exactly what the keying makes unnecessary.
func (m *profileMemo[T]) invalidate(profile string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if strings.TrimSpace(profile) == "" {
		m.entries = map[string]memoEntry[T]{}
		return
	}
	delete(m.entries, profile)
}

// coldFetch runs `fetch` for a profile that has NOTHING cached, at most once
// across concurrent callers. The leader fetches; everyone else waits and is
// handed the same result.
//
// It deliberately does not store anything itself: `fetch` already has to,
// because the uncached path is what stamps FetchedAt and decides whether a
// partial result is worth memoising at all.
func (m *profileMemo[T]) coldFetch(profile string, fetch func(string) (T, error)) (T, error) {
	f, leader := m.claim(profile)
	if !leader {
		<-f.done
		// Safe without the lock: the leader writes both fields before closing.
		return f.value, f.err
	}
	return m.fly(profile, f, fetch)
}

// revalidate refreshes profile behind the caller, at most one fetch per
// profile at a time -- shared with coldFetch, so a cold caller can join.
//
// `fetch` is expected to store its own result on success (the uncached path
// already has to), so the result is discarded here. Errors are dropped on
// purpose: the caller already has rows on screen, and replacing them with an
// error banner because a background poll failed would be worse than showing
// slightly old data. The next explicit refresh surfaces the failure.
func (m *profileMemo[T]) revalidate(profile string, fetch func(string) (T, error)) {
	f, leader := m.claim(profile)
	if !leader {
		return // someone is already fetching; nothing to add
	}
	go func() { _, _ = m.fly(profile, f, fetch) }()
}

// claim returns the in-flight entry for profile and whether the caller owns
// it. The owner must call fly.
func (m *profileMemo[T]) claim(profile string) (*memoFlight[T], bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if f, ok := m.inflight[profile]; ok {
		return f, false
	}
	f := &memoFlight[T]{done: make(chan struct{})}
	m.inflight[profile] = f
	return f, true
}

// fly runs the fetch the caller claimed, publishes its result to any waiters
// and releases the claim.
func (m *profileMemo[T]) fly(profile string, f *memoFlight[T], fetch func(string) (T, error)) (T, error) {
	defer func() {
		m.mu.Lock()
		if m.inflight[profile] == f {
			delete(m.inflight, profile)
		}
		m.mu.Unlock()
		close(f.done)
	}()
	f.value, f.err = fetch(profile)
	return f.value, f.err
}

// age backdates an entry, for tests that need one to read as stale.
func (m *profileMemo[T]) age(profile string, by time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	e, ok := m.entries[profile]
	if !ok {
		return
	}
	e.at = e.at.Add(-by)
	m.entries[profile] = e
}
