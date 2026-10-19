package main

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"phantom-ink/brainbox"
	"phantom-ink/provider"
)

// The four outcomes used to be indistinguishable: the binding returned
// interface{} and the panel caught any throw into {configured:false,
// exists:false}, so a registry 401 rendered exactly like "no image
// configured". That is the recorded registry-auth gotcha, and it was
// unfixable in the UI because the Go signature carried no reason to branch on.

func TestClassifyRegistryErrorDetectsUnauthorized(t *testing.T) {
	err := &provider.StatusError{Code: http.StatusUnauthorized}
	if got := classifyRegistryError(err); got != ImageStateUnauthorized {
		t.Errorf("401 classified as %q, want unauthorized", got)
	}
}

func TestClassifyRegistryErrorDefaultsToUnreachable(t *testing.T) {
	if got := classifyRegistryError(errors.New("dial tcp: connection refused")); got != ImageStateUnreachable {
		t.Errorf("connection refused classified as %q, want unreachable", got)
	}
}

// The router relays the registry's complaint as free text, so the message is
// all there is to go on for that path.
func TestClassifyRegistryMessage(t *testing.T) {
	unauthorized := []string{
		"HTTP 401: unauthorized",
		"403 Forbidden",
		"authentication required",
		"denied: requested access to the resource is denied",
		"UNAUTHORIZED: authentication required",
	}
	for _, msg := range unauthorized {
		if got := classifyRegistryMessage(msg); got != ImageStateUnauthorized {
			t.Errorf("%q classified as %q, want unauthorized", msg, got)
		}
	}

	// Deliberately narrow: anything without an auth signal is a reachability
	// problem, not a guess at auth.
	other := []string{"i/o timeout", "no such host", "manifest unknown", ""}
	for _, msg := range other {
		if got := classifyRegistryMessage(msg); got != ImageStateUnreachable {
			t.Errorf("%q classified as %q, want unreachable", msg, got)
		}
	}
}

func imageStatusServer(t *testing.T, code int, body string) *brainbox.Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(code)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return brainbox.NewClient(srv.URL, "")
}

func TestProfileImageStatusPresent(t *testing.T) {
	a := &App{client: imageStatusServer(t, 200,
		`{"configured":true,"exists":true,"tag":"v1","digest":"sha256:abc"}`)}
	got := a.profileImageStatus("personal")
	if got.State != ImageStatePresent {
		t.Errorf("state = %q, want present", got.State)
	}
	if got.Tag != "v1" {
		t.Errorf("tag = %q", got.Tag)
	}
}

func TestProfileImageStatusMissing(t *testing.T) {
	a := &App{client: imageStatusServer(t, 200, `{"configured":true,"exists":false}`)}
	if got := a.profileImageStatus("personal"); got.State != ImageStateMissing {
		t.Errorf("state = %q, want missing", got.State)
	}
}

func TestProfileImageStatusUnconfigured(t *testing.T) {
	a := &App{client: imageStatusServer(t, 200, `{"configured":false,"exists":false}`)}
	if got := a.profileImageStatus("personal"); got.State != ImageStateUnconfigured {
		t.Errorf("state = %q, want unconfigured", got.State)
	}
}

// The case that motivated all of this: a 401 must NOT read as "no image".
func TestProfileImageStatusUnauthorizedIsNotMissing(t *testing.T) {
	a := &App{client: imageStatusServer(t, 200,
		`{"configured":true,"exists":false,"error":"401 unauthorized: check REGISTRY_PASSWORD"}`)}
	got := a.profileImageStatus("personal")
	if got.State == ImageStateMissing {
		t.Error("a registry 401 still reads as 'no image' — the original bug")
	}
	if got.State != ImageStateUnauthorized {
		t.Errorf("state = %q, want unauthorized", got.State)
	}
	if got.Detail == "" {
		t.Error("no detail carried, so the UI cannot say what to fix")
	}
}

func TestProfileImageStatusTransportFailureIsUnreachable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := srv.URL
	srv.Close() // nothing listening
	a := &App{client: brainbox.NewClient(url, "")}

	got := a.profileImageStatus("personal")
	if got.State != ImageStateUnreachable {
		t.Errorf("state = %q, want unreachable", got.State)
	}
}

// A nil client is a configuration problem, not a crash.
func TestProfileImageStatusNilClient(t *testing.T) {
	got := (&App{}).profileImageStatus("personal")
	if got.State != ImageStateUnreachable || got.Detail == "" {
		t.Errorf("nil client: state=%q detail=%q", got.State, got.Detail)
	}
}

// The batched call returns one entry per requested profile.
func TestGetRemoteProfileImageStatusesBatches(t *testing.T) {
	a := &App{client: imageStatusServer(t, 200, `{"configured":true,"exists":true,"tag":"v1"}`)}
	out, err := a.GetRemoteProfileImageStatuses([]string{"personal", "gsa", "lakeview"})
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 3 {
		t.Fatalf("got %d entries, want 3", len(out))
	}
	for _, name := range []string{"personal", "gsa", "lakeview"} {
		st, ok := out[name]
		if !ok {
			t.Errorf("%s missing from the batch", name)
			continue
		}
		if st.Profile != name {
			t.Errorf("%s: profile echoed back as %q", name, st.Profile)
		}
	}
}

func TestGetRemoteProfileImageStatusesSkipsBlankNames(t *testing.T) {
	a := &App{client: imageStatusServer(t, 200, `{"configured":true,"exists":true}`)}
	out, err := a.GetRemoteProfileImageStatuses([]string{"personal", "", "   "})
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 1 {
		t.Errorf("got %d entries, want 1 (blanks skipped)", len(out))
	}
}

func TestGetRemoteProfileImageStatusesEmptyInput(t *testing.T) {
	out, err := (&App{}).GetRemoteProfileImageStatuses(nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 0 {
		t.Errorf("got %d entries for no input", len(out))
	}
}
