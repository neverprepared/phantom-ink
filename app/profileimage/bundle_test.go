package profileimage

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// unpack reads a bundle tar.gz into (manifest, files-by-name).
func unpack(t *testing.T, tarGz []byte) (Manifest, map[string][]byte) {
	t.Helper()
	gz, err := gzip.NewReader(bytes.NewReader(tarGz))
	if err != nil {
		t.Fatalf("gzip: %v", err)
	}
	tr := tar.NewReader(gz)
	files := map[string][]byte{}
	var manifest Manifest
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatalf("tar: %v", err)
		}
		data, _ := io.ReadAll(tr)
		if hdr.Name == "manifest.json" {
			if err := json.Unmarshal(data, &manifest); err != nil {
				t.Fatalf("manifest: %v", err)
			}
			continue
		}
		files[hdr.Name] = data
	}
	return manifest, files
}

func write(t *testing.T, path string, data string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
}

func awsSource(t *testing.T) BundleSource {
	t.Helper()
	src, ok := CatalogSource("aws")
	if !ok {
		t.Fatal("aws not in catalog")
	}
	return src
}

// sandboxHome keeps the catalog's $HOME fallbacks from resolving into the
// developer's real home dir (a test once captured live SSO tokens…).
func sandboxHome(t *testing.T) {
	t.Helper()
	t.Setenv("HOME", t.TempDir())
}

func TestCollect_WorkspaceHomeDefault(t *testing.T) {
	sandboxHome(t)
	ws := t.TempDir()
	write(t, filepath.Join(ws, ".aws", "config"), "[default]\nregion=us-east-1\n")
	write(t, filepath.Join(ws, ".aws", "credentials"), "[default]\nkey=x\n")

	res, err := CollectBundle("p", ws, []ResolvedSource{{Source: awsSource(t), Kind: "catalog"}}, nil, "t")
	if err != nil {
		t.Fatal(err)
	}
	manifest, files := unpack(t, res.TarGz)
	if len(manifest.Entries) != 1 || manifest.Entries[0].Source != "aws" {
		t.Fatalf("entries = %+v", manifest.Entries)
	}
	if !strings.Contains(string(files["aws/config"]), "us-east-1") {
		t.Errorf("aws/config content wrong: %q", files["aws/config"])
	}
	env := manifest.Entries[0].Env
	if env["AWS_CONFIG_FILE"] != "aws/config" || env["AWS_SHARED_CREDENTIALS_FILE"] != "aws/credentials" {
		t.Errorf("env map = %v", env)
	}
}

func TestCollect_EnvOverrideWins(t *testing.T) {
	sandboxHome(t)
	ws := t.TempDir()
	write(t, filepath.Join(ws, ".aws", "config"), "wrong")
	write(t, filepath.Join(ws, "custom-aws.conf"), "override-content")
	write(t, filepath.Join(ws, ".env"), `AWS_CONFIG_FILE="$WORKSPACE_HOME/custom-aws.conf"`+"\n")

	res, err := CollectBundle("p", ws, []ResolvedSource{{Source: awsSource(t), Kind: "catalog"}}, nil, "t")
	if err != nil {
		t.Fatal(err)
	}
	_, files := unpack(t, res.TarGz)
	if string(files["aws/config"]) != "override-content" {
		t.Errorf("override var did not win: %q", files["aws/config"])
	}
}

func TestCollect_MissingSourceOmitted(t *testing.T) {
	sandboxHome(t)
	ws := t.TempDir() // nothing exists
	res, err := CollectBundle("p", ws, []ResolvedSource{{Source: awsSource(t), Kind: "catalog"}}, nil, "t")
	if err != nil {
		t.Fatal(err)
	}
	manifest, _ := unpack(t, res.TarGz)
	if len(manifest.Entries) != 0 {
		t.Errorf("absent source must be omitted, got %+v", manifest.Entries)
	}
}

func TestCollect_SizeCapSkipsWithWarning(t *testing.T) {
	sandboxHome(t)
	ws := t.TempDir()
	write(t, filepath.Join(ws, ".aws", "config"), "small")
	big := make([]byte, perSourceCapBytes+1)
	if err := os.WriteFile(filepath.Join(ws, ".aws", "credentials"), big, 0o600); err != nil {
		t.Fatal(err)
	}

	res, err := CollectBundle("p", ws, []ResolvedSource{{Source: awsSource(t), Kind: "catalog"}}, nil, "t")
	if err != nil {
		t.Fatal(err)
	}
	manifest, files := unpack(t, res.TarGz)
	if _, ok := files["aws/credentials"]; ok {
		t.Error("oversized file must be skipped")
	}
	if len(res.Warnings) == 0 {
		t.Error("size-cap skip must warn")
	}
	if _, ok := files["aws/config"]; !ok {
		t.Error("small sibling must still be captured")
	}
	if manifest.Entries[0].Env["AWS_SHARED_CREDENTIALS_FILE"] != "" {
		t.Error("skipped file must not get an env mapping")
	}
}

func TestCollect_CustomSourceWithEnvMap(t *testing.T) {
	sandboxHome(t)
	ws := t.TempDir()
	write(t, filepath.Join(ws, ".snowsql", "config"), "[connections]\n")

	src, err := CustomSource("snowflake", CustomSourceDef{
		Globs:    []string{"$WORKSPACE_HOME/.snowsql/config"},
		Audience: AudienceBoth,
	})
	if err != nil {
		t.Fatal(err)
	}
	customEnv := map[string]map[string]string{
		"snowflake": {"SNOWSQL_CONFIG": "snowflake/config"},
	}
	res, err := CollectBundle("p", ws, []ResolvedSource{{Source: src, Kind: "custom"}}, customEnv, "t")
	if err != nil {
		t.Fatal(err)
	}
	manifest, files := unpack(t, res.TarGz)
	if _, ok := files["snowflake/config"]; !ok {
		t.Fatalf("custom file missing; files=%v", files)
	}
	e := manifest.Entries[0]
	if e.Kind != "custom" || e.Audiences[0] != AudienceBoth {
		t.Errorf("entry = %+v", e)
	}
	if e.Env["SNOWSQL_CONFIG"] != "snowflake/config" {
		t.Errorf("custom env map missing: %v", e.Env)
	}
}

func TestCustomSource_Validation(t *testing.T) {
	sandboxHome(t)
	if _, err := CustomSource("../evil", CustomSourceDef{Globs: []string{"x"}}); err == nil {
		t.Error("path-traversal name must be rejected")
	}
	src, err := CustomSource("ok", CustomSourceDef{Globs: []string{"x"}})
	if err != nil {
		t.Fatal(err)
	}
	if src.Audience != AudienceSession {
		t.Errorf("default audience = %q, want session", src.Audience)
	}
}

func TestCollect_SSHIsSessionAudience(t *testing.T) {
	sandboxHome(t)
	ws := t.TempDir()
	write(t, filepath.Join(ws, ".ssh", "id_ed25519"), "KEY")
	src, _ := CatalogSource("ssh")
	res, err := CollectBundle("p", ws, []ResolvedSource{{Source: src, Kind: "catalog"}}, nil, "t")
	if err != nil {
		t.Fatal(err)
	}
	manifest, files := unpack(t, res.TarGz)
	if manifest.Entries[0].Audiences[0] != AudienceSession {
		t.Errorf("ssh audience = %v, want session", manifest.Entries[0].Audiences)
	}
	if _, ok := files["ssh/id_ed25519"]; !ok {
		t.Errorf("ssh key not captured; files=%v", files)
	}
	if len(manifest.Entries[0].Env) != 0 {
		t.Errorf("ssh must have no env mappings: %v", manifest.Entries[0].Env)
	}
}
