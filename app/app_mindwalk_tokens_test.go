package main

import "testing"

// The container is pointed at the LOCAL brain daemon, so its tokens should come
// from the same place: the target profile's own .env.
func TestVaultTokensFromEnvText(t *testing.T) {
	text := `
# comment
export CL_BRAIN_API_TOKEN="memtok"
CL_SKILLS_API_TOKEN=skilltok
CL_TODO_API_TOKEN='todotok'
CL_AGENTS_API_TOKEN=
CL_API_KEY=not-a-vault-token
GITHUB_TOKEN=unrelated
`
	got := vaultTokensFromEnvText(text)

	want := map[string]string{
		"memory": "memtok",
		"skills": "skilltok",
		"todo":   "todotok",
	}
	if len(got) != len(want) {
		t.Fatalf("got %d vaults %v, want %d %v", len(got), keysOf(got), len(want), keysOf(want))
	}
	for vault, tok := range want {
		if got[vault] != tok {
			t.Errorf("vault %q = %q, want %q", vault, got[vault], tok)
		}
	}
	if _, ok := got["agents"]; ok {
		t.Error("an empty token value must not produce a vault entry")
	}
}

func keysOf(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// Local tokens win per-vault, but the router can still fill vaults the local env
// does not carry — neither source is all-or-nothing.
func TestMergeVaultTokens(t *testing.T) {
	cases := []struct {
		name          string
		local, router map[string]string
		want          map[string]string
	}{
		{
			name:  "local only",
			local: map[string]string{"memory": "L"},
			want:  map[string]string{"memory": "L"},
		},
		{
			name:   "router only",
			router: map[string]string{"memory": "R"},
			want:   map[string]string{"memory": "R"},
		},
		{
			name:   "local wins, router fills the gap",
			local:  map[string]string{"memory": "L"},
			router: map[string]string{"memory": "R", "skills": "RS"},
			want:   map[string]string{"memory": "L", "skills": "RS"},
		},
		{
			name: "both empty",
			want: map[string]string{},
		},
	}

	for _, c := range cases {
		got := mergeVaultTokens(c.local, c.router)
		if len(got) != len(c.want) {
			t.Errorf("%s: got %v, want %v", c.name, got, c.want)
			continue
		}
		for k, v := range c.want {
			if got[k] != v {
				t.Errorf("%s: vault %q = %q, want %q", c.name, k, got[k], v)
			}
		}
	}
}
