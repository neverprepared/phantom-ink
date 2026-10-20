package main

import "testing"

// classifyRegistryMessage reads a free-text message, so a bare digit match is
// a trap: an ephemeral port, a byte count or a digest can all carry "401" or
// "403" without the registry having rejected anything. These cases pin the
// distinction, because getting it wrong tells an operator to go fix a
// credential when the registry is simply not reachable.
//
// The port case is not hypothetical. It made
// TestProfileImageStatusTransportFailureIsUnreachable fail roughly 2% of runs
// (9 in 400 locally), and cluster in CI, because Linux hands out ephemeral
// ports near-sequentially: a runner whose allocator sits in a 403xx window
// fails that test every time.
func TestClassifyRegistryMessageIgnoresDigitsInsideLongerNumbers(t *testing.T) {
	unreachable := []struct{ name, msg string }{
		{
			// The exact shape that flaked: the port contains "403".
			name: "ephemeral port containing 403",
			msg:  "Get \"http://127.0.0.1:40399/api/profile-images\": dial tcp 127.0.0.1:40399: connect: connection refused",
		},
		{
			name: "ephemeral port containing 401",
			msg:  "dial tcp 127.0.0.1:34012: connect: connection refused",
		},
		{
			name: "byte count containing 403",
			msg:  "unexpected EOF after 14031 bytes",
		},
		{
			name: "digest containing 401",
			msg:  "manifest sha256:be4019f3c0d1 not found",
		},
		{
			name: "plain transport failure",
			msg:  "context deadline exceeded",
		},
	}
	for _, c := range unreachable {
		t.Run(c.name, func(t *testing.T) {
			if got := classifyRegistryMessage(c.msg); got != ImageStateUnreachable {
				t.Errorf("classifyRegistryMessage(%q) = %q, want %q",
					c.msg, got, ImageStateUnreachable)
			}
		})
	}
}

// The narrowing must not cost us the auth signals the badge exists to show.
func TestClassifyRegistryMessageStillCatchesRealAuthFailures(t *testing.T) {
	unauthorized := []struct{ name, msg string }{
		{"standalone 401", "registry returned 401"},
		{"standalone 403", "HTTP/1.1 403 Forbidden"},
		{"status prefix", "status 403: pull access denied"},
		{"word unauthorized", "UNAUTHORIZED: authentication required"},
		{"word forbidden", "Forbidden"},
		{"registry denied", "denied: requested access to the resource is denied"},
	}
	for _, c := range unauthorized {
		t.Run(c.name, func(t *testing.T) {
			if got := classifyRegistryMessage(c.msg); got != ImageStateUnauthorized {
				t.Errorf("classifyRegistryMessage(%q) = %q, want %q",
					c.msg, got, ImageStateUnauthorized)
			}
		})
	}
}
