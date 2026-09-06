package profileimage

import "testing"

// CL_BRAIN_API points at 127.0.0.1:9998 on the host — dead inside a container.
// The bake must rewrite the loopback host to host.docker.internal (preserving
// port + scheme) so pbrainctl reaches the runner host's daemon, WITHOUT touching
// the var name or any non-URL value (e.g. a bearer token that happens to contain
// digits).
func TestRewriteLoopbackForContainer(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{"CL_BRAIN_API=http://127.0.0.1:9998", "CL_BRAIN_API=http://host.docker.internal:9998"},
		{"CL_BRAIN_API=http://localhost:9998/v1", "CL_BRAIN_API=http://host.docker.internal:9998/v1"},
		{"BRAINBOX_URL=https://api.neverprepared.com", "BRAINBOX_URL=https://api.neverprepared.com"},
		// Token values must be untouched even if they contain "localhost"-like text.
		{"CL_BRAIN_API_TOKEN=deadbeeflocalhost127", "CL_BRAIN_API_TOKEN=deadbeeflocalhost127"},
		// No '=' → returned verbatim.
		{"not an assignment", "not an assignment"},
		// The var NAME is never rewritten, only the value.
		{"localhost_API=http://localhost:9998", "localhost_API=http://host.docker.internal:9998"},
	}
	for _, c := range cases {
		if got := rewriteLoopbackForContainer(c.in); got != c.want {
			t.Errorf("rewriteLoopbackForContainer(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}
