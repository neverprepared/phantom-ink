package doctor

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestServiceHealthAllHealthyIsPass(t *testing.T) {
	ex := &fakeExec{services: []Service{
		{Name: "router", State: "running", Health: "healthy"},
		{Name: "events", State: "running", Health: ""},
	}}
	got := ServiceHealthCheck{}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
}

// A one-shot init sidecar is exempt from the health scan even when its
// healthcheck reports unhealthy — the exemption is the point of the field, and
// the service would otherwise land in "bad" like any other unhealthy service.
// Contrast: TestServiceHealthUnhealthyIsWarnPointingAtServices flags the same
// Health value on a non-one-shot service.
func TestServiceHealthOneShotUnhealthyIsExempt(t *testing.T) {
	ex := &fakeExec{services: []Service{
		{Name: "brain-init", State: "exited", Health: "unhealthy", OneShot: true},
	}}
	got := ServiceHealthCheck{}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass for an exempted one-shot", got.State)
	}
}

func TestServiceHealthUnhealthyIsWarnPointingAtServices(t *testing.T) {
	ex := &fakeExec{services: []Service{
		{Name: "router", State: "running", Health: "unhealthy"},
	}}
	got := ServiceHealthCheck{}.Run(context.Background(), ex)
	if got.State != StateWarn {
		t.Fatalf("state = %q, want warn", got.State)
	}
	if !strings.Contains(got.Detail, "router") {
		t.Fatalf("detail %q must name the service", got.Detail)
	}
	// Restarting a compose service is an app action, not a shell command, and
	// the Services panel already does it one click away.
	if got.Remedy == nil || got.Remedy.AutoSafe {
		t.Fatal("want a non-auto-safe remedy pointing at the Services panel")
	}
}

func TestServiceHealthUnreadableIsUnknown(t *testing.T) {
	ex := &fakeExec{servicesErr: errors.New("node unreachable")}
	got := ServiceHealthCheck{}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
}

func TestTraefikRunningAndReachableIsPass(t *testing.T) {
	ex := &fakeExec{
		services: []Service{{Name: "traefik", State: "running", Addr: "localhost:8080"}},
		probes:   map[string]fakeProbe{"http://localhost:8080": {status: 200}},
	}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
}

// The orphan-nginx signature: the container is up, but the port answers to
// something else or not at all.
func TestTraefikRunningButUnreachableIsFail(t *testing.T) {
	ex := &fakeExec{
		services: []Service{{Name: "traefik", State: "running", Addr: "localhost:8080"}},
		probes:   map[string]fakeProbe{"http://localhost:8080": {err: errors.New("connection refused")}},
	}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	if !strings.Contains(got.Detail, "running") {
		t.Fatalf("detail %q should say the container is up but unreachable", got.Detail)
	}
}

func TestTraefikNotRunningIsFail(t *testing.T) {
	ex := &fakeExec{services: []Service{{Name: "traefik", State: "exited"}}}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
}

func TestTraefikAbsentIsUnknown(t *testing.T) {
	ex := &fakeExec{services: []Service{{Name: "router", State: "running"}}}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown when the service is not in the stack", got.State)
	}
}

func TestTraefikNoPublishedPortIsUnknown(t *testing.T) {
	ex := &fakeExec{services: []Service{{Name: "traefik", State: "running", Addr: ""}}}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown with no published port to probe", got.State)
	}
}

// Regression: with a remote platform node configured, Service.Addr is the
// literal "localhost:<port>" the RUNNER derived on the node, so probing it from
// here observes the operator's own Mac. The fixture makes that danger concrete —
// a healthy HTTP 200 is stubbed at exactly the URL the check would build, which
// is what a dev Traefik or the documented orphan nginx on the operator's laptop
// would produce. If the RemoteNode guard is removed, this test reports StatePass
// off that fixture: a confident "traefik answers" having never touched the
// subject, which is the false-pass class this package exists to prevent.
func TestTraefikOnRemoteNodeIsUnknownNotPass(t *testing.T) {
	ex := &fakeExec{
		services: []Service{{Name: "traefik", State: "running", Addr: "localhost:8080"}},
		probes:   map[string]fakeProbe{"http://localhost:8080": {status: 200}},
	}
	got := TraefikBoundCheck{ServiceName: "traefik", RemoteNode: "m3-64"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown — a remote node's published port is not probeable from this host (detail %q)", got.State, got.Detail)
	}
	if !strings.Contains(got.Detail, "m3-64") {
		t.Fatalf("detail %q must name the node it could not reach", got.Detail)
	}
}

// Companion: the remote guard must not swallow the local case. With no node
// configured the probe still runs and a refused connection is still the
// orphan-nginx fail — a guard that short-circuits unconditionally would make
// the whole check inert.
func TestTraefikLocalStillProbesWhenNoNodeConfigured(t *testing.T) {
	ex := &fakeExec{
		services: []Service{{Name: "traefik", State: "running", Addr: "localhost:8080"}},
		probes:   map[string]fakeProbe{"http://localhost:8080": {err: errors.New("connection refused")}},
	}
	got := TraefikBoundCheck{ServiceName: "traefik", RemoteNode: ""}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail (detail %q)", got.State, got.Detail)
	}
}
