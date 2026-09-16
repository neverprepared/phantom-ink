package doctor

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"syscall"
	"testing"
)

func TestParseEnv(t *testing.T) {
	content := `# a comment

export CL_BRAIN_API="http://127.0.0.1:9998"
CL_BRAIN_API_TOKEN=abc123
QUOTED='single'
EMPTY=
SPACED = value
INLINE=value # trailing comment
NOT_AN_ASSIGNMENT
=NOKEY
`
	got := ParseEnv(content)
	want := map[string]string{
		"CL_BRAIN_API":       "http://127.0.0.1:9998",
		"CL_BRAIN_API_TOKEN": "abc123",
		"QUOTED":             "single",
		"EMPTY":              "",
		"SPACED":             "value",
		"INLINE":             "value",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ParseEnv() = %v, want %v", got, want)
	}
}

func TestMissingKeys(t *testing.T) {
	example := map[string]string{
		"PRESENT": "x",
		"MISSING": "x",
		"EMPTY":   "x",
		"BLANK":   "x",
	}
	profile := map[string]string{
		"PRESENT": "value",
		"EMPTY":   "",
		"BLANK":   "   ",
		"EXTRA":   "not reported",
	}

	missing, empty := MissingKeys(profile, example)

	if !reflect.DeepEqual(missing, []string{"MISSING"}) {
		t.Errorf("missing = %v, want [MISSING]", missing)
	}
	if !reflect.DeepEqual(empty, []string{"BLANK", "EMPTY"}) {
		t.Errorf("empty = %v, want [BLANK EMPTY]", empty)
	}
}

func TestMissingKeys_AllSet(t *testing.T) {
	missing, empty := MissingKeys(
		map[string]string{"A": "1", "B": "2"},
		map[string]string{"A": "", "B": ""},
	)
	if len(missing) != 0 || len(empty) != 0 {
		t.Errorf("expected no missing/empty, got %v / %v", missing, empty)
	}
}

func TestReadEnvFile(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, ".env", "A=1\nB=2\n")

	got, err := ReadEnvFile(filepath.Join(dir, ".env"))
	if err != nil {
		t.Fatalf("ReadEnvFile() error: %v", err)
	}
	if got["A"] != "1" || got["B"] != "2" {
		t.Errorf("got %v", got)
	}
}

func TestReadEnvFile_Missing(t *testing.T) {
	_, err := ReadEnvFile(filepath.Join(t.TempDir(), "nope"))
	if !os.IsNotExist(err) {
		t.Errorf("expected IsNotExist, got %v", err)
	}
}

// A 1Password-style FIFO with no writer must never be read inline — doctor has
// to stay responsive, so it reports the file as unreadable instead of hanging.
func TestReadEnvFile_FIFOIsNotRead(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env.secrets")
	if err := syscall.Mkfifo(path, 0o600); err != nil {
		t.Skipf("cannot create FIFO on this platform: %v", err)
	}

	done := make(chan error, 1)
	go func() {
		_, err := ReadEnvFile(path)
		done <- err
	}()

	select {
	case err := <-done:
		if !errors.Is(err, errUnreadable) {
			t.Errorf("expected errUnreadable, got %v", err)
		}
	case <-timeoutAfter():
		t.Fatal("ReadEnvFile blocked on a FIFO — it must never hang")
	}
}

func TestLoadProfile_UnreadableSecretsIsNoted(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, ".env", "A=1\n")
	path := filepath.Join(dir, ".env.secrets")
	if err := syscall.Mkfifo(path, 0o600); err != nil {
		t.Skipf("cannot create FIFO on this platform: %v", err)
	}

	p := LoadProfile("demo", dir, newStub(t, nil).factory())
	if p.Secrets != nil {
		t.Error("secrets from an unreadable FIFO must not be populated")
	}
	if p.SecretsNote == "" {
		t.Error("expected a note explaining why secrets were not read")
	}
}
