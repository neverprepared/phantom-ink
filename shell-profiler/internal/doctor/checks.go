package doctor

import (
	"fmt"
	"os"
	"strings"
)

// Categories a check can belong to.
const (
	CatStructure = "structure"
	CatEnv       = "env"
	CatGitHub    = "github"
	CatBrain     = "brain"
	CatRouter    = "router"
	CatCloud     = "cloud"
)

// DefaultChecks is the catalog, in report order: presence first, then live
// checks, then the conditional cloud checks.
func DefaultChecks() []Check {
	checks := []Check{
		checkEnvFile(),
		checkEnvrcFile(),
		checkDirenvAllowed(),
		checkGitconfig(),
		checkExpectedKeys(),
		checkGitHubSSH(),
		checkGitHubCLI(),
		checkBrainDaemon(),
	}
	for _, v := range brainVaults {
		checks = append(checks, checkBrainToken(v))
	}
	checks = append(checks,
		checkRouter(),
		checkAWS(),
		checkAzure(),
		checkGCloud(),
	)
	return checks
}

// --- structure ---------------------------------------------------------

func checkEnvFile() Check {
	return NewCheck("profile env file", CatStructure, func(p *Profile) Result {
		if !p.HasEnvFile {
			return fail(EnvFileName+" is missing",
				fmt.Sprintf("create %s — copy %s and fill in the values", p.Path(EnvFileName), ExampleEnvFileName))
		}
		return ok(fmt.Sprintf("%s present (%d keys)", EnvFileName, len(p.Env)))
	})
}

func checkEnvrcFile() Check {
	return NewCheck("direnv file", CatStructure, func(p *Profile) Result {
		if !p.Exists(EnvrcFileName) {
			return fail(EnvrcFileName+" is missing",
				fmt.Sprintf("run: shell-profiler update %s", p.Name))
		}
		return ok(EnvrcFileName + " present")
	})
}

func checkDirenvAllowed() Check {
	return NewCheck("direnv allowed", CatStructure, func(p *Profile) Result {
		if !p.Exists(EnvrcFileName) {
			return skip(EnvrcFileName + " missing — nothing to allow")
		}
		out, err := p.Run("direnv", "status")
		if isNotInstalled(err) {
			return skip("direnv is not installed — install it to load this profile")
		}
		if err != nil && strings.TrimSpace(out) == "" {
			return skip("direnv status failed: " + p.Redact(firstLine(err.Error())))
		}
		if strings.Contains(out, "Found RC allowed true") || strings.Contains(out, "Found RC allowed 0") {
			return ok("direnv has allowed " + EnvrcFileName)
		}
		return fail("direnv has not allowed "+EnvrcFileName,
			fmt.Sprintf("run: cd %s && direnv allow", p.Dir))
	})
}

func checkGitconfig() Check {
	return NewCheck("gitconfig", CatStructure, func(p *Profile) Result {
		path := p.Path(GitconfigFileName)
		data, err := os.ReadFile(path) //nolint:gosec // path is the profile's own gitconfig
		if err != nil {
			return fail(GitconfigFileName+" is missing",
				fmt.Sprintf("create %s with a [user] name and email", path))
		}
		name := gitConfigValue(string(data), "name")
		email := gitConfigValue(string(data), "email")
		var unset []string
		if name == "" {
			unset = append(unset, "user.name")
		}
		if email == "" {
			unset = append(unset, "user.email")
		}
		if len(unset) > 0 {
			return fail(GitconfigFileName+" missing "+strings.Join(unset, ", "),
				fmt.Sprintf("set them in %s under [user]", path))
		}
		return ok(fmt.Sprintf("%s <%s>", name, email))
	})
}

// gitConfigValue extracts a `key = value` from the [user] section of a
// gitconfig. Deliberately simple: gitconfig files written by this tool are
// flat, and shelling out to git would need a live binary.
func gitConfigValue(content, key string) string {
	inUser := false
	for _, line := range strings.Split(content, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") {
			inUser = strings.HasPrefix(trimmed, "[user]")
			continue
		}
		if !inUser {
			continue
		}
		k, v, found := strings.Cut(trimmed, "=")
		if !found || strings.TrimSpace(k) != key {
			continue
		}
		return strings.Trim(strings.TrimSpace(v), `"`)
	}
	return ""
}

// --- env / tokens ------------------------------------------------------

func checkExpectedKeys() Check {
	return NewCheck("expected env keys", CatEnv, func(p *Profile) Result {
		if !p.HasExample {
			return skip(ExampleEnvFileName + " not found — no expected-key list for this profile")
		}
		if !p.HasEnvFile {
			return fail(EnvFileName+" is missing, so every expected key is unset",
				fmt.Sprintf("cp %s %s and fill in the values", p.Path(ExampleEnvFileName), p.Path(EnvFileName)))
		}
		missing, empty := MissingKeys(p.Env, p.Example)
		if len(missing) == 0 && len(empty) == 0 {
			return ok(fmt.Sprintf("all %d keys from %s are set", len(p.Example), ExampleEnvFileName))
		}
		// Key NAMES only — never values.
		var parts []string
		if len(missing) > 0 {
			parts = append(parts, "missing: "+strings.Join(missing, ", "))
		}
		if len(empty) > 0 {
			parts = append(parts, "empty: "+strings.Join(empty, ", "))
		}
		return fail(strings.Join(parts, "; "),
			fmt.Sprintf("add the keys above to %s — see %s", p.Path(EnvFileName), ExampleEnvFileName))
	})
}

// --- github ------------------------------------------------------------

func checkGitHubSSH() Check {
	return NewCheck("github ssh", CatGitHub, func(p *Profile) Result {
		out, err := p.Run("ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-T", "git@github.com")
		if isNotInstalled(err) {
			return skip("ssh is not installed")
		}
		// GitHub always exits 1 on `ssh -T`; the greeting is the success signal.
		if strings.Contains(strings.ToLower(out), "successfully authenticated") {
			return ok(p.Redact(firstLine(out)))
		}
		if isUnreachable(out, err) {
			return skip("github.com unreachable over ssh — check the network")
		}
		return fail("ssh key not accepted by github.com",
			"add this profile's ssh key to GitHub: ssh-keygen then gh ssh-key add")
	})
}

func checkGitHubCLI() Check {
	return NewCheck("gh auth", CatGitHub, func(p *Profile) Result {
		out, err := p.Run("gh", "auth", "status")
		if isNotInstalled(err) {
			return skip("gh is not installed")
		}
		if err == nil {
			return ok("gh is authenticated")
		}
		if isUnreachable(out, err) {
			return skip("github.com unreachable — gh auth status could not run")
		}
		return fail("gh is not authenticated", "run: gh auth login")
	})
}

// --- brain -------------------------------------------------------------

// brainVault describes one phantom-brain vault and the env keys that reach it.
type brainVault struct {
	label    string
	tokenKey string
	// required marks the vault whose token every profile is expected to carry.
	required bool
}

var brainVaults = []brainVault{
	{label: "memory", tokenKey: "CL_BRAIN_API_TOKEN", required: true},
	{label: "todo", tokenKey: "CL_TODO_API_TOKEN"},
	{label: "skills", tokenKey: "CL_SKILLS_API_TOKEN"},
	{label: "agents", tokenKey: "CL_AGENTS_API_TOKEN"},
}

func checkBrainDaemon() Check {
	return NewCheck("brain daemon", CatBrain, func(p *Profile) Result {
		url, found := p.Lookup("CL_BRAIN_API")
		if !found {
			return fail("CL_BRAIN_API is not set",
				fmt.Sprintf("add CL_BRAIN_API to %s — see %s", EnvFileName, ExampleEnvFileName))
		}
		out, err := p.Run("curl", "-sS", "--max-time", "5", "-o", "/dev/null", "-w", "%{http_code}", url)
		if isNotInstalled(err) {
			return skip("curl is not installed — cannot probe the brain daemon")
		}
		code := strings.TrimSpace(firstLine(out))
		if err != nil || code == "000" || code == "" {
			return skip(fmt.Sprintf("brain daemon at %s unreachable — start it", url))
		}
		return ok(fmt.Sprintf("daemon at %s responded (HTTP %s)", url, code))
	})
}

func checkBrainToken(v brainVault) Check {
	return NewCheck("brain token ("+v.label+")", CatBrain, func(p *Profile) Result {
		token, found := p.Lookup(v.tokenKey)
		if !found {
			if !v.required {
				return skip(v.tokenKey + " not set — " + v.label + " vault not configured for this profile")
			}
			return fail(v.tokenKey+" is not set",
				fmt.Sprintf("add %s to %s — see %s", v.tokenKey, EnvFileName, ExampleEnvFileName))
		}
		// The token reaches pbrainctl through the child environment only.
		run := p.RunnerWithEnv(map[string]string{"CL_BRAIN_API_TOKEN": token})
		out, err := run("pbrainctl", "client", "recall", "--limit", "1", "doctor")
		if isNotInstalled(err) {
			return skip("pbrainctl is not installed — cannot verify " + v.tokenKey)
		}
		if err == nil {
			return ok(v.tokenKey + " is valid")
		}
		if isUnreachable(out, err) {
			return skip("brain daemon unreachable — could not verify " + v.tokenKey + "; start the daemon")
		}
		if isUnauthorized(out, err) {
			return fail(v.tokenKey+" was rejected by the brain daemon",
				fmt.Sprintf("rotate %s and update %s", v.tokenKey, EnvFileName))
		}
		return fail(v.tokenKey+" check failed: "+p.Redact(firstLine(out)),
			fmt.Sprintf("verify %s in %s", v.tokenKey, EnvFileName))
	})
}

// isUnauthorized reports whether output describes a rejected credential.
func isUnauthorized(output string, err error) bool {
	hay := strings.ToLower(output)
	if err != nil {
		hay += " " + strings.ToLower(err.Error())
	}
	for _, m := range []string{"401", "403", "unauthorized", "forbidden", "invalid token", "authentication failed", "permission denied"} {
		if strings.Contains(hay, m) {
			return true
		}
	}
	return false
}

// --- router ------------------------------------------------------------

func checkRouter() Check {
	return NewCheck("router", CatRouter, func(p *Profile) Result {
		url, hasURL := p.Lookup("CL_ROUTER_API")
		_, hasKey := p.Lookup("CL_API_KEY")
		var unset []string
		if !hasURL {
			unset = append(unset, "CL_ROUTER_API")
		}
		if !hasKey {
			unset = append(unset, "CL_API_KEY")
		}
		if len(unset) > 0 {
			return fail(strings.Join(unset, " and ")+" not set",
				fmt.Sprintf("add %s to %s — see %s", strings.Join(unset, ", "), EnvFileName, ExampleEnvFileName))
		}
		out, err := p.Run("prouterctl", "status")
		if isNotInstalled(err) {
			return skip("prouterctl is not installed — cannot verify the router")
		}
		if err == nil {
			return ok("router at " + url + " reachable, CL_API_KEY accepted")
		}
		if isUnreachable(out, err) {
			return skip(fmt.Sprintf("router at %s unreachable — start it", url))
		}
		if isUnauthorized(out, err) {
			return fail("CL_API_KEY was rejected by the router",
				fmt.Sprintf("rotate CL_API_KEY and update %s", EnvFileName))
		}
		return fail("prouterctl status failed: "+p.Redact(firstLine(out)),
			fmt.Sprintf("check CL_ROUTER_API and CL_API_KEY in %s", EnvFileName))
	})
}

// --- cloud (conditional) -----------------------------------------------

func checkAWS() Check {
	return NewCheck("aws", CatCloud, func(p *Profile) Result {
		if !p.DirExists(".aws") {
			return skip("no .aws directory — AWS not configured for this profile")
		}
		out, err := p.Run("aws", "sts", "get-caller-identity")
		if isNotInstalled(err) {
			return skip("aws cli is not installed")
		}
		if err == nil {
			return ok("aws credentials are valid")
		}
		if isUnreachable(out, err) {
			return skip("aws endpoint unreachable — check the network")
		}
		return fail("aws credentials are not valid", "run: aws sso login (or refresh this profile's credentials)")
	})
}

func checkAzure() Check {
	return NewCheck("azure", CatCloud, func(p *Profile) Result {
		if !p.DirExists(".azure-profiles") && !p.DirExists(".azure") {
			return skip("no .azure-profiles directory — Azure not configured for this profile")
		}
		out, err := p.Run("az", "account", "show")
		if isNotInstalled(err) {
			return skip("az cli is not installed")
		}
		if err == nil {
			return ok("azure account is active")
		}
		if isUnreachable(out, err) {
			return skip("azure endpoint unreachable — check the network")
		}
		return fail("no active azure account", "run: az login")
	})
}

func checkGCloud() Check {
	return NewCheck("gcloud", CatCloud, func(p *Profile) Result {
		if !p.DirExists(".gcloud") {
			return skip("no .gcloud directory — GCP not configured for this profile")
		}
		out, err := p.Run("gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)")
		if isNotInstalled(err) {
			return skip("gcloud cli is not installed")
		}
		if isUnreachable(out, err) {
			return skip("gcloud endpoint unreachable — check the network")
		}
		if err == nil && strings.TrimSpace(out) != "" {
			return ok("gcloud has an active account")
		}
		return fail("no active gcloud account", "run: gcloud auth login")
	})
}
