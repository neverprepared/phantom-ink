package main

import (
	"errors"
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
