package main

import "testing"

// The Enabled toggle is a power switch for local docker integrations: flipping it
// starts or stops the container. Native services are managed outside docker and
// remote ones live on another host, so for those the toggle only records config.
func TestServiceToggleAction(t *testing.T) {
	local := ServiceDef{Name: "mindwalk", Port: 9997}
	native := ServiceDef{Name: "phantom-brain-mesh", Native: true}
	platform := ServiceDef{Name: "langfuse", Platform: true}

	cases := []struct {
		name    string
		def     ServiceDef
		cfg     ServiceConfig
		enabled bool
		want    toggleAction
	}{
		{"local on starts", local, ServiceConfig{}, true, toggleStart},
		{"local off stops", local, ServiceConfig{}, false, toggleStop},
		{"native on is config only", native, ServiceConfig{}, true, toggleConfigOnly},
		{"native off is config only", native, ServiceConfig{}, false, toggleConfigOnly},
		{"remote on is config only", local, ServiceConfig{Remote: true}, true, toggleConfigOnly},
		{"remote off is config only", local, ServiceConfig{Remote: true}, false, toggleConfigOnly},
		{"platform is config only", platform, ServiceConfig{}, true, toggleConfigOnly},
	}

	for _, c := range cases {
		if got := serviceToggleAction(c.def, c.cfg, c.enabled); got != c.want {
			t.Errorf("%s: serviceToggleAction() = %v, want %v", c.name, got, c.want)
		}
	}
}
