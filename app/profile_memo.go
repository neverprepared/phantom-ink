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
//   - single-flight, so a panel mount landing while a refresh is already in
//     flight does not fan the whole provider set out twice;
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

	mu           sync.Mutex
	entries      map[string]memoEntry[T]
	revalidating map[string]bool
}

type memoEntry[T any] struct {
	value T
	at    time.Time
}

func newProfileMemo[T any](ttl time.Duration) *profileMemo[T] {
	return &profileMemo[T]{
		ttl:          ttl,
		entries:      map[string]memoEntry[T]{},
		revalidating: map[string]bool{},
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

// revalidate refreshes profile behind the caller, at most one refresh per
// profile at a time.
//
// `fetch` is expected to store its own result on success (the uncached path
// already has to), so the result is discarded here. Errors are dropped on
// purpose: the caller already has rows on screen, and replacing them with an
// error banner because a background poll failed would be worse than showing
// slightly old data. The next explicit refresh surfaces the failure.
func (m *profileMemo[T]) revalidate(profile string, fetch func(string) (T, error)) {
	m.mu.Lock()
	if m.revalidating[profile] {
		m.mu.Unlock()
		return
	}
	m.revalidating[profile] = true
	m.mu.Unlock()

	go func() {
		defer func() {
			m.mu.Lock()
			delete(m.revalidating, profile)
			m.mu.Unlock()
		}()
		_, _ = fetch(profile)
	}()
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
