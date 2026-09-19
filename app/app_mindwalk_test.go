package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestContainerBrainURL(t *testing.T) {
	cases := map[string]string{
		"http://localhost:9998":                "http://host.docker.internal:9998",
		"http://127.0.0.1:9998":                "http://host.docker.internal:9998",
		"http://api.neverprepared.com:9998":    "http://api.neverprepared.com:9998",
		"http://pbrain.neverprepared.com:9998": "http://pbrain.neverprepared.com:9998",
	}
	for in, want := range cases {
		if got := containerBrainURL(in); got != want {
			t.Errorf("containerBrainURL(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestResolveMindwalkRepo_HonorsEnv(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "Dockerfile"), []byte("FROM scratch\n"), 0644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MINDWALK_REPO", dir)

	got, err := resolveMindwalkRepo()
	if err != nil {
		t.Fatalf("resolveMindwalkRepo() error = %v", err)
	}
	want, _ := filepath.Abs(dir)
	if got != want {
		t.Errorf("resolveMindwalkRepo() = %q, want %q", got, want)
	}
}

func TestResolveMindwalkRepo_EnvWithoutDockerfileFallsThrough(t *testing.T) {
	// A MINDWALK_REPO pointing at a dir with no Dockerfile must NOT be accepted;
	// it should be skipped in favor of the sibling-repo probe (or error).
	empty := t.TempDir()
	t.Setenv("MINDWALK_REPO", empty)

	got, _ := resolveMindwalkRepo()
	wantEmpty, _ := filepath.Abs(empty)
	if got == wantEmpty {
		t.Errorf("resolveMindwalkRepo() accepted %q which has no Dockerfile", got)
	}
}

func TestServiceComposeEnv_NilForOtherServices(t *testing.T) {
	a := &App{}
	got, err := a.serviceComposeEnv("langfuse")
	if err != nil {
		t.Fatalf("serviceComposeEnv(langfuse) error = %v", err)
	}
	if got != nil {
		t.Errorf("serviceComposeEnv(langfuse) = %v, want nil", got)
	}
}

func TestKnownServices_MindwalkRegistered(t *testing.T) {
	var found *ServiceDef
	for i := range knownServices {
		if knownServices[i].Name == mindwalkServiceName {
			found = &knownServices[i]
			break
		}
	}
	if found == nil {
		t.Fatal("mindwalk not registered in knownServices")
	}
	if found.Port != 9997 {
		t.Errorf("mindwalk Port = %d, want 9997", found.Port)
	}
	if found.Native {
		t.Error("mindwalk must not be Native (it is a docker integration)")
	}
	if found.Platform {
		t.Error("mindwalk must not be Platform (it is a normal Integration)")
	}
}
