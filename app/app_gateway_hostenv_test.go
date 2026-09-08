package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadHostEnvText_BothFilesSecretsLast(t *testing.T) {
	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, ".env"), []byte("FOO=bar\nGITHUB_TOKEN=plain\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ws, ".env.secrets"), []byte("GITHUB_TOKEN=secret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	text, err := readHostEnvText(ws)
	if err != nil {
		t.Fatalf("readHostEnvText: %v", err)
	}
	// .env content must precede .env.secrets so the editor's later-wins merge
	// lets the secret override the plain value.
	iPlain := strings.Index(text, "GITHUB_TOKEN=plain")
	iSecret := strings.Index(text, "GITHUB_TOKEN=secret")
	if iPlain < 0 || iSecret < 0 {
		t.Fatalf("expected both GITHUB_TOKEN lines, got:\n%s", text)
	}
	if iSecret < iPlain {
		t.Errorf(".env.secrets must come AFTER .env (so it wins); got secret at %d, plain at %d", iSecret, iPlain)
	}
	if !strings.Contains(text, "FOO=bar") {
		t.Errorf("missing .env-only var FOO: %s", text)
	}
}

func TestReadHostEnvText_SecretsOnly(t *testing.T) {
	ws := t.TempDir() // no .env, only .env.secrets — the env-only profile case
	if err := os.WriteFile(filepath.Join(ws, ".env.secrets"), []byte("GITHUB_TOKEN=secret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	text, err := readHostEnvText(ws)
	if err != nil {
		t.Fatalf("env-only profile (.env.secrets only) should not error: %v", err)
	}
	if !strings.Contains(text, "GITHUB_TOKEN=secret") {
		t.Errorf("missing secret: %s", text)
	}
}

func TestReadHostEnvText_NeitherFileErrors(t *testing.T) {
	if _, err := readHostEnvText(t.TempDir()); err == nil {
		t.Fatal("expected an error when neither .env nor .env.secrets exists")
	}
}
