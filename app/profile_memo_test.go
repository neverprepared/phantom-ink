package main

import (
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestProfileMemoServesStaleFlagged(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	m.store("work", "rows")

	if v, fresh, ok := m.get("work"); !ok || !fresh || v != "rows" {
		t.Fatalf("fresh read: v=%q fresh=%v ok=%v", v, fresh, ok)
	}

	m.age("work", 2*time.Minute)
	v, fresh, ok := m.get("work")
	if !ok {
		t.Fatal("a stale entry was withheld; the caller has nothing to paint")
	}
	if fresh {
		t.Error("a stale entry reported itself fresh")
	}
	if v != "rows" {
		t.Errorf("v = %q, want \"rows\"", v)
	}
}

func TestProfileMemoMissReportsNotOK(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	if _, _, ok := m.get("work"); ok {
		t.Error("an empty memo reported a hit")
	}
}

// Keying is what makes the memo safe: one profile must never read another's.
func TestProfileMemoIsPerProfile(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	m.store("alpha", "a-rows")
	m.store("beta", "b-rows")

	if v, _, _ := m.get("alpha"); v != "a-rows" {
		t.Errorf("alpha read %q", v)
	}
	if v, _, _ := m.get("beta"); v != "b-rows" {
		t.Errorf("beta read %q", v)
	}
}

func TestProfileMemoInvalidateIsTargeted(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	m.store("alpha", "a")
	m.store("beta", "b")

	m.invalidate("alpha")
	if _, _, ok := m.get("alpha"); ok {
		t.Error("alpha should have been dropped")
	}
	if _, _, ok := m.get("beta"); !ok {
		t.Error("beta should have been left alone")
	}
}

func TestProfileMemoInvalidateAllOnEmptyName(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	m.store("alpha", "a")
	m.store("beta", "b")

	m.invalidate("   ") // whitespace counts as empty
	if _, _, ok := m.get("alpha"); ok {
		t.Error("alpha survived a wipe")
	}
	if _, _, ok := m.get("beta"); ok {
		t.Error("beta survived a wipe")
	}
}

// Concurrent callers on a stale key must collapse into ONE refresh.
func TestProfileMemoRevalidateIsSingleFlight(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	m.store("work", "old")
	m.age("work", 2*time.Minute)

	release := make(chan struct{})
	var calls atomic.Int64
	fetch := func(profile string) (string, error) {
		calls.Add(1)
		<-release
		m.store(profile, "new")
		return "new", nil
	}

	for i := 0; i < 8; i++ {
		m.revalidate("work", fetch)
	}
	waitForMemo(t, func() bool { return calls.Load() >= 1 }, "no revalidate started")
	close(release)

	waitForMemo(t, func() bool {
		_, fresh, ok := m.get("work")
		return ok && fresh
	}, "revalidate never refreshed the entry")

	if got := calls.Load(); got != 1 {
		t.Errorf("%d revalidates ran; single-flight did not collapse them", got)
	}
}

// A second revalidate is allowed once the first has finished -- the guard is
// "one at a time", not "once ever".
func TestProfileMemoRevalidateUnlocksAfterCompletion(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	var calls atomic.Int64
	fetch := func(profile string) (string, error) {
		calls.Add(1)
		return "", nil
	}

	m.store("work", "old")
	m.age("work", 2*time.Minute)
	m.revalidate("work", fetch)
	waitForMemo(t, func() bool { return calls.Load() == 1 }, "first revalidate did not run")

	m.revalidate("work", fetch)
	waitForMemo(t, func() bool { return calls.Load() == 2 }, "a later revalidate was still blocked")
}

// A failed revalidate must leave the previous value in place rather than
// blanking it: the caller has rows on screen and a background poll failing is
// not a reason to take them away.
func TestProfileMemoRevalidateFailureKeepsPreviousValue(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	m.store("work", "old")
	m.age("work", 2*time.Minute)

	var calls atomic.Int64
	fetch := func(string) (string, error) {
		calls.Add(1)
		return "", errors.New("provider down")
	}
	m.revalidate("work", fetch)
	waitForMemo(t, func() bool { return calls.Load() == 1 }, "revalidate did not run")

	v, fresh, ok := m.get("work")
	if !ok || v != "old" {
		t.Errorf("v=%q ok=%v; a failed revalidate lost the previous value", v, ok)
	}
	if fresh {
		t.Error("a failed revalidate marked the entry fresh")
	}
}

func waitForMemo(t *testing.T, cond func() bool, msg string) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal(msg)
}

// ── Cold-path single-flight ───────────────────────────────────────────────
//
// The Jira tab mount fires two cold readers of the SAME fan-out: the code
// panel's own refresh and the ticket list's join. Without this, both miss and
// the whole 686-repo provider fan-out runs twice in parallel.

func TestProfileMemoColdFetchCoalescesConcurrentMisses(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	var calls atomic.Int64
	release := make(chan struct{})
	fetch := func(profile string) (string, error) {
		calls.Add(1)
		<-release
		m.store(profile, "fanned-out")
		return "fanned-out", nil
	}

	const readers = 6
	var wg sync.WaitGroup
	got := make([]string, readers)
	errs := make([]error, readers)
	for i := 0; i < readers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			got[i], errs[i] = m.coldFetch("work", fetch)
		}(i)
	}
	waitForMemo(t, func() bool { return calls.Load() >= 1 }, "no cold fetch started")
	time.Sleep(30 * time.Millisecond) // let the rest pile up behind it
	close(release)
	wg.Wait()

	if n := calls.Load(); n != 1 {
		t.Fatalf("%d fetches ran for %d concurrent cold readers; want 1", n, readers)
	}
	for i := range got {
		if errs[i] != nil {
			t.Fatalf("reader %d: %v", i, errs[i])
		}
		if got[i] != "fanned-out" {
			t.Fatalf("reader %d got %q; a waiter did not receive the leader's result", i, got[i])
		}
	}
}

// The guard is "one at a time", not "once ever".
func TestProfileMemoColdFetchAllowsALaterFetch(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	var calls atomic.Int64
	fetch := func(profile string) (string, error) {
		calls.Add(1)
		return "v", nil
	}

	if _, err := m.coldFetch("work", fetch); err != nil {
		t.Fatalf("first: %v", err)
	}
	if _, err := m.coldFetch("work", fetch); err != nil {
		t.Fatalf("second: %v", err)
	}
	if n := calls.Load(); n != 2 {
		t.Fatalf("calls = %d, want 2: coldFetch must not memoise by itself", n)
	}
}

// A failure reaches every waiter, and nothing is left claimed.
func TestProfileMemoColdFetchPropagatesFailureToWaiters(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	release := make(chan struct{})
	var calls atomic.Int64
	fetch := func(string) (string, error) {
		calls.Add(1)
		<-release
		return "", errors.New("providers down")
	}

	var wg sync.WaitGroup
	errs := make([]error, 3)
	for i := 0; i < 3; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, errs[i] = m.coldFetch("work", fetch)
		}(i)
	}
	waitForMemo(t, func() bool { return calls.Load() >= 1 }, "no cold fetch started")
	time.Sleep(30 * time.Millisecond)
	close(release)
	wg.Wait()

	for i, err := range errs {
		if err == nil {
			t.Fatalf("waiter %d saw no error", i)
		}
	}
	// Still usable afterwards: the operator fixes the provider and retries.
	ok := func(string) (string, error) { return "v", nil }
	if v, err := m.coldFetch("work", ok); err != nil || v != "v" {
		t.Fatalf("after a failure: %q / %v", v, err)
	}
}

// Two profiles must not wait on each other.
func TestProfileMemoColdFetchIsPerProfile(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	release := make(chan struct{})
	t.Cleanup(func() { close(release) })
	go func() {
		_, _ = m.coldFetch("slow", func(string) (string, error) {
			<-release
			return "slow", nil
		})
	}()
	time.Sleep(20 * time.Millisecond)

	done := make(chan string, 1)
	go func() {
		v, _ := m.coldFetch("fast", func(string) (string, error) { return "fast", nil })
		done <- v
	}()
	select {
	case v := <-done:
		if v != "fast" {
			t.Fatalf("got %q", v)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("a second profile's cold fetch blocked behind the first")
	}
}

// A cold caller arriving while a BACKGROUND revalidate is already running must
// wait for it rather than start a second fan-out. (Reachable because
// invalidate can drop the entry mid-revalidate.)
func TestProfileMemoColdFetchPiggybacksOnARevalidate(t *testing.T) {
	m := newProfileMemo[string](time.Minute)
	var calls atomic.Int64
	release := make(chan struct{})
	fetch := func(profile string) (string, error) {
		calls.Add(1)
		<-release
		m.store(profile, "refreshed")
		return "refreshed", nil
	}

	m.store("work", "old")
	m.age("work", 2*time.Minute)
	m.revalidate("work", fetch)
	waitForMemo(t, func() bool { return calls.Load() == 1 }, "revalidate did not start")

	m.invalidate("work") // the entry goes away mid-flight
	got := make(chan string, 1)
	go func() {
		v, _ := m.coldFetch("work", fetch)
		got <- v
	}()
	time.Sleep(30 * time.Millisecond)
	close(release)

	select {
	case v := <-got:
		if v != "refreshed" {
			t.Fatalf("cold caller got %q", v)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("cold caller never returned")
	}
	if n := calls.Load(); n != 1 {
		t.Fatalf("fetches = %d, want 1: the cold caller did not piggyback", n)
	}
}
