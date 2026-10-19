package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// dirSize walks an unbounded tree (CLAUDE_CONFIG_DIR and the sessions dir both
// grow without limit) and was being re-walked on every poll tick by overlapping
// 10s/30s UI timers. Memoised for statsCacheTTL.
func TestDirSizeIsMemoised(t *testing.T) {
	dir := t.TempDir()
	write := func(name string, size int) {
		if err := os.WriteFile(filepath.Join(dir, name), make([]byte, size), 0o600); err != nil {
			t.Fatalf("write: %v", err)
		}
	}
	write("a", 1000)

	resetDirSizeCache()
	first := dirSize(dir)
	if first != 1000 {
		t.Fatalf("first = %d, want 1000", first)
	}

	// Grow the tree. Within the TTL the cached answer must stand.
	write("b", 500)
	if got := dirSize(dir); got != first {
		t.Errorf("within TTL: got %d, want the cached %d", got, first)
	}

	// Expire the entry and the new size must be picked up.
	expireDirSizeCache(dir)
	if got := dirSize(dir); got != 1500 {
		t.Errorf("after expiry: got %d, want 1500", got)
	}
}

// Each path is cached independently — one directory's entry must not serve
// another's.
func TestDirSizeCacheIsPerPath(t *testing.T) {
	a, b := t.TempDir(), t.TempDir()
	if err := os.WriteFile(filepath.Join(a, "f"), make([]byte, 700), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(b, "f"), make([]byte, 200), 0o600); err != nil {
		t.Fatal(err)
	}
	resetDirSizeCache()

	if got := dirSize(a); got != 700 {
		t.Errorf("a = %d, want 700", got)
	}
	if got := dirSize(b); got != 200 {
		t.Errorf("b = %d, want 200 (a's entry leaked)", got)
	}
}

// A missing directory must be 0 rather than an error or a panic, and must not
// poison the cache for a path that later exists.
func TestDirSizeMissingPath(t *testing.T) {
	resetDirSizeCache()
	missing := filepath.Join(t.TempDir(), "nope")
	if got := dirSize(missing); got != 0 {
		t.Errorf("missing dir = %d, want 0", got)
	}
}

// resetDirSizeCache clears the memo so a test starts from a known state.
func resetDirSizeCache() {
	dirSizeMu.Lock()
	dirSizeCache = map[string]dirSizeEntry{}
	dirSizeMu.Unlock()
}

// expireDirSizeCache ages one entry past the TTL without sleeping.
func expireDirSizeCache(path string) {
	dirSizeMu.Lock()
	if e, ok := dirSizeCache[path]; ok {
		e.at = time.Now().Add(-2 * statsCacheTTL)
		dirSizeCache[path] = e
	}
	dirSizeMu.Unlock()
}

// workspacesRoot must prefer the in-memory config. defaultWorkspacesRoot()
// opens a second read-only SQLite handle on the same WAL file, and was being
// called from the env-refresh timer for a value already in memory.
func TestWorkspacesRootPrefersConfig(t *testing.T) {
	a := &App{config: &Config{WorkspacesRoot: "/configured/root"}}
	if got := a.workspacesRoot(); got != "/configured/root" {
		t.Errorf("workspacesRoot = %q, want the configured value", got)
	}
}

// With no configured root -- or no config at all, which is the real bootstrap
// case -- it must fall back rather than return empty or nil-deref. scanProfiles
// would otherwise be handed "" and silently find nothing.
func TestWorkspacesRootFallsBackWhenUnset(t *testing.T) {
	// config is a *Config and is nil until loadConfigFromDB runs.
	if got := (&App{}).workspacesRoot(); got == "" {
		t.Error("nil config: workspacesRoot returned empty")
	}
	a := &App{config: &Config{WorkspacesRoot: "   "}}
	if got := a.workspacesRoot(); got == "" {
		t.Error("workspacesRoot returned empty; expected the bootstrap fallback")
	}
}

// FindClaudeProcesses is a host process scan, and the Sessions panel runs two
// timers that each want it (a 30s refresh and a 10s metrics tick) — so at every
// 30s boundary it ran twice within milliseconds. It is cached like the docker
// reads; this pins the cache rather than the scan itself, which depends on what
// is running on the machine.
func TestFindClaudeProcessesIsCached(t *testing.T) {
	a := &App{}

	resetLocalProcCache()
	first, err1 := a.FindClaudeProcesses()
	at := localProcAt
	if at.IsZero() {
		t.Fatal("first call did not record a cache timestamp")
	}

	second, err2 := a.FindClaudeProcesses()
	if !localProcAt.Equal(at) {
		t.Error("second call re-ran the scan instead of serving the cache")
	}
	if len(first) != len(second) {
		t.Errorf("cached result differs: %d vs %d", len(first), len(second))
	}
	if (err1 == nil) != (err2 == nil) {
		t.Error("cached error state differs between calls")
	}

	// Past the TTL it must scan again.
	localProcMu.Lock()
	localProcAt = time.Now().Add(-2 * statsCacheTTL)
	localProcMu.Unlock()
	if _, err := a.FindClaudeProcesses(); err != nil && err != err1 {
		t.Logf("post-expiry scan returned %v", err)
	}
	if localProcAt.Before(at) {
		t.Error("an expired entry was not refreshed")
	}
}

func resetLocalProcCache() {
	localProcMu.Lock()
	localProcAt = time.Time{}
	localProcVal = nil
	localProcErr = nil
	localProcOK = false
	localProcMu.Unlock()
}

// An EMPTY result must still count as cached. This is the case CI hit and a
// dev machine hides: with no claude/codex/ollama running, the scan's grep exits
// non-zero and findClaudeProcesses returns (nil, nil). Inferring "nothing
// cached" from val==nil && err==nil meant the cache never applied on exactly
// the machines where the scan is cheapest to skip — so the flag is explicit.
func TestEmptyScanResultIsStillCached(t *testing.T) {
	a := &App{}

	// Seed the cache as if a scan had just found nothing.
	localProcMu.Lock()
	localProcVal = nil
	localProcErr = nil
	localProcOK = true
	localProcAt = time.Now()
	localProcMu.Unlock()

	at := localProcAt
	got, err := a.FindClaudeProcesses()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != nil {
		t.Errorf("expected the cached empty result, got %d entries", len(got))
	}
	if !localProcAt.Equal(at) {
		t.Error("an empty cached result was discarded and the scan re-ran")
	}
}

// Same property for the container-disk cache: a host with no containers
// yields (nil, nil) and that must be cached too.
func TestEmptyContainerDiskResultIsStillCached(t *testing.T) {
	a := &App{}

	containerDiskMu.Lock()
	containerDiskVal = nil
	containerDiskErr = nil
	containerDiskOK = true
	containerDiskAt = time.Now()
	containerDiskMu.Unlock()

	at := containerDiskAt
	if _, err := a.GetContainerDiskUsage(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !containerDiskAt.Equal(at) {
		t.Error("an empty cached result was discarded and docker ran again")
	}
}
