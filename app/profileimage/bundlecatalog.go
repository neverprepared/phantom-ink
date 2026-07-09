package profileimage

// Credential-source catalog for per-profile bundles.
//
// A BundleSource describes WHERE a well-known credential lives on the
// operator's machine and WHICH env vars point at it once materialized.
// Path resolution mirrors brainbox's session-mount conventions
// (lifecycle.py): a profile-env override var wins, then the profile's
// $WORKSPACE_HOME copy, then ~. The daemon never sees this catalog —
// each capture writes the resolved facts into the bundle manifest, so
// operator-defined custom sources ride the exact same pipeline.
//
// Every source defaults OFF; the operator opts in per profile.

import (
	"os"
	"path/filepath"
	"strings"
)

// Audience values match the manifest contract consumed by gateway_bundle.py.
const (
	AudienceGateway = "gateway"
	AudienceSession = "session"
	AudienceBoth    = "both"
)

// BundleItem is one file (or glob of files) a source captures.
type BundleItem struct {
	// Rel is the destination path inside the bundle, under the source name
	// (e.g. "config" → "aws/config"). For Glob items it is the destination
	// directory; matches land there by basename.
	Rel string
	// OverrideVar, when set in the profile env, names the file directly
	// (mount-table convention: AWS_CONFIG_FILE wins over the default path).
	OverrideVar string
	// Candidates are default paths tried in order; "$WORKSPACE_HOME"/"$HOME"/
	// "~" expand against the profile. First existing wins.
	Candidates []string
	// Glob marks Candidates as glob patterns collecting every match.
	Glob bool
	// EnvVar, when non-empty, is written into the manifest env map pointing
	// at Rel — the var consumers inject at materialization.
	EnvVar string
}

// BundleSource is one toggleable credential source.
type BundleSource struct {
	Name     string
	Label    string
	Audience string
	Items    []BundleItem
	// MaxBytes caps this source's total captured size (0 = default cap).
	MaxBytes int64
}

// perSourceCapBytes is the default per-source size cap.
const perSourceCapBytes = 2 << 20 // 2 MiB

// totalCapBytes caps the whole plaintext bundle; must stay under the
// daemon's CL_GATEWAY__BUNDLE_MAX_BYTES (10 MiB default).
const totalCapBytes = 8 << 20 // 8 MiB

// Catalog returns the built-in credential sources.
//
// Note on aws sso/cache: the AWS CLI derives the SSO token-cache path from
// $HOME, not from AWS_CONFIG_FILE, so the cache only helps consumers whose
// HOME is the materialization root — session containers, not the gateway
// host. It is therefore session-audience with no env mapping.
func Catalog() []BundleSource {
	return []BundleSource{
		{
			Name: "aws", Label: "AWS", Audience: AudienceBoth,
			Items: []BundleItem{
				{Rel: "config", OverrideVar: "AWS_CONFIG_FILE",
					Candidates: []string{"$WORKSPACE_HOME/.aws/config", "$HOME/.aws/config"},
					EnvVar:     "AWS_CONFIG_FILE"},
				{Rel: "credentials", OverrideVar: "AWS_SHARED_CREDENTIALS_FILE",
					Candidates: []string{"$WORKSPACE_HOME/.aws/credentials", "$HOME/.aws/credentials"},
					EnvVar:     "AWS_SHARED_CREDENTIALS_FILE"},
				{Rel: "sso-cache", Glob: true,
					Candidates: []string{"$WORKSPACE_HOME/.aws/sso/cache/*.json", "$HOME/.aws/sso/cache/*.json"}},
			},
		},
		{
			Name: "azure", Label: "Azure", Audience: AudienceBoth,
			Items: []BundleItem{
				{Rel: "azureProfile.json",
					Candidates: []string{"$AZURE_CONFIG_DIR/azureProfile.json", "$WORKSPACE_HOME/.azure/azureProfile.json", "$HOME/.azure/azureProfile.json"}},
				{Rel: "msal_token_cache.json",
					Candidates: []string{"$AZURE_CONFIG_DIR/msal_token_cache.json", "$WORKSPACE_HOME/.azure/msal_token_cache.json", "$HOME/.azure/msal_token_cache.json"}},
				{Rel: "service_principal_entries.json",
					Candidates: []string{"$AZURE_CONFIG_DIR/service_principal_entries.json", "$WORKSPACE_HOME/.azure/service_principal_entries.json", "$HOME/.azure/service_principal_entries.json"}},
			},
		},
		{
			Name: "kube", Label: "Kubernetes", Audience: AudienceBoth,
			Items: []BundleItem{
				{Rel: "config", OverrideVar: "KUBECONFIG",
					Candidates: []string{"$WORKSPACE_HOME/.kube/config", "$HOME/.kube/config"},
					EnvVar:     "KUBECONFIG"},
			},
		},
		{
			Name: "git", Label: "Git config", Audience: AudienceBoth,
			Items: []BundleItem{
				{Rel: "gitconfig", OverrideVar: "GIT_CONFIG_GLOBAL",
					Candidates: []string{"$WORKSPACE_HOME/.gitconfig", "$HOME/.gitconfig"},
					EnvVar:     "GIT_CONFIG_GLOBAL"},
			},
		},
		{
			Name: "terraform", Label: "Terraform", Audience: AudienceBoth,
			Items: []BundleItem{
				{Rel: "terraformrc", OverrideVar: "TF_CLI_CONFIG_FILE",
					Candidates: []string{"$WORKSPACE_HOME/.terraformrc", "$HOME/.terraformrc"},
					EnvVar:     "TF_CLI_CONFIG_FILE"},
			},
		},
		{
			// SSH keys never land on the gateway host — session audience only,
			// and still explicit opt-in like everything else.
			Name: "ssh", Label: "SSH keys", Audience: AudienceSession,
			Items: []BundleItem{
				{Rel: ".", Glob: true,
					Candidates: []string{"$WORKSPACE_HOME/.ssh/*", "$HOME/.ssh/*"}},
			},
		},
	}
}

// CatalogSource looks a built-in source up by name.
func CatalogSource(name string) (BundleSource, bool) {
	for _, s := range Catalog() {
		if s.Name == name {
			return s, true
		}
	}
	return BundleSource{}, false
}

// expandPath resolves $WORKSPACE_HOME / $HOME / ~ / arbitrary $VARs against
// the profile env (with the OS env and home dir as fallbacks).
func expandPath(p, workspaceHome string, profileEnv map[string]string) string {
	home, _ := os.UserHomeDir()
	expanded := os.Expand(p, func(key string) string {
		switch key {
		case "WORKSPACE_HOME":
			return workspaceHome
		case "HOME":
			return home
		}
		if v, ok := profileEnv[key]; ok {
			return expandPath(v, workspaceHome, nil) // one nested level ($WORKSPACE_HOME inside values)
		}
		return os.Getenv(key)
	})
	if strings.HasPrefix(expanded, "~/") {
		expanded = filepath.Join(home, expanded[2:])
	}
	return expanded
}
