package main

import (
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
