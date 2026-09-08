package profileimage

import (
	"os"
	"path/filepath"
	"testing"
)

// writeEnv writes a file under dir, failing the test on error.
func writeEnv(t *testing.T, dir, name, content string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o600); err != nil {
		t.Fatalf("write %s: %v", name, err)
	}
}

func TestMergeEnvFiles_ParsesAndMerges(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, ".env", `
# a comment
FOO=bar
export BAZ=qux
QUOTED="hello world"
SINGLE='single quoted'
GITHUB_TOKEN=plain-from-env
NOEQUALS
`)
	// secrets win on overlap.
	writeEnv(t, dir, ".env.secrets", `
GITHUB_TOKEN=secret-from-secrets
ONLYSECRET=s3cr3t
`)

	got, err := MergeEnvFiles(dir)
	if err != nil {
		t.Fatalf("MergeEnvFiles: %v", err)
	}

	want := map[string]string{
		"FOO":          "bar",
		"BAZ":          "qux",
		"QUOTED":       "hello world",
		"SINGLE":       "single quoted",
		"GITHUB_TOKEN": "secret-from-secrets", // secrets override .env
		"ONLYSECRET":   "s3cr3t",
	}
	if len(got) != len(want) {
		t.Fatalf("key count = %d, want %d; got=%v", len(got), len(want), got)
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s = %q, want %q", k, got[k], v)
		}
	}
	if _, ok := got["NOEQUALS"]; ok {
		t.Errorf("line without '=' should be skipped, got NOEQUALS=%q", got["NOEQUALS"])
	}
}

func TestMergeEnvFiles_MissingFilesAreEmptyNotError(t *testing.T) {
	dir := t.TempDir() // neither file exists
	got, err := MergeEnvFiles(dir)
	if err != nil {
		t.Fatalf("MergeEnvFiles with no files should not error: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("expected empty map, got %v", got)
	}
}

func TestMergeEnvFiles_OnlyPlainEnv(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, ".env", "A=1\nB=2\n")
	got, err := MergeEnvFiles(dir)
	if err != nil {
		t.Fatalf("MergeEnvFiles: %v", err)
	}
	if got["A"] != "1" || got["B"] != "2" || len(got) != 2 {
		t.Fatalf("got %v", got)
	}
}

// A value that is only a single quote char (len 1) must not be treated as
// quoted (guards the len>=2 unquote condition).
func TestMergeEnvFiles_ShortValueNotUnquoted(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, ".env", `Q="`+"\n"+`EMPTY=`+"\n")
	got, err := MergeEnvFiles(dir)
	if err != nil {
		t.Fatalf("MergeEnvFiles: %v", err)
	}
	if got["Q"] != `"` {
		t.Errorf(`Q = %q, want a lone double-quote`, got["Q"])
	}
	if v, ok := got["EMPTY"]; !ok || v != "" {
		t.Errorf("EMPTY should be present and empty, got ok=%v v=%q", ok, v)
	}
}
