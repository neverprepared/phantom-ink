package doctor

import (
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Profile is the context a Check runs against: the profile's on-disk location,
// its parsed env files, and the injectable command runner.
//
// Env holds real values (they must reach subprocesses), so nothing here may be
// printed directly. Redact scrubs any known secret value out of a string and is
// applied to every Result before it leaves Run.
type Profile struct {
	Name string
	Dir  string

	// Env is the parsed profile env file.
	Env map[string]string
	// Example is the parsed example env file (the expected-key source of truth).
	Example map[string]string
	// Secrets is the parsed secrets env file; nil when it is absent or could
	// not be read safely (e.g. a 1Password FIFO).
	Secrets map[string]string
	// SecretsNote explains why Secrets is nil, when it is.
	SecretsNote string
	// HasExample records whether an example env file was found at all.
	HasExample bool
	// HasEnvFile records whether the profile env file was found at all.
	HasEnvFile bool

	// IsCurrent marks the profile whose direnv environment is the one actually
	// loaded in this process. Only that profile may have its credentials read
	// out of the live environment — for any other profile the live env belongs
	// to someone else and would be a false verdict. See effectiveSecret.
	IsCurrent bool

	// liveSecrets holds secret values read from the live process environment
	// (never from a profile file), so Redact scrubs them too.
	liveSecrets []string

	// Run executes external commands. Always set; never nil.
	Run CmdRunner

	// newRunner builds a runner for this profile's directory with a given env
	// overlay. Kept so checks that need a different token in the child
	// environment (e.g. a second brain vault) can derive a runner without ever
	// putting a token on a command line.
	newRunner func(dir string, env map[string]string) CmdRunner
}

// RunnerWithEnv returns a CmdRunner whose child environment is this profile's
// env plus the supplied overrides.
func (p *Profile) RunnerWithEnv(overrides map[string]string) CmdRunner {
	if p.newRunner == nil {
		return p.Run
	}
	env := p.subprocessEnv()
	for k, v := range overrides {
		env[k] = v
	}
	return p.newRunner(p.Dir, env)
}

// Lookup returns a value from the profile env file, falling back to the
// secrets env file. The boolean reports whether a non-empty value was found.
func (p *Profile) Lookup(key string) (string, bool) {
	if v, okv := p.Env[key]; okv && strings.TrimSpace(v) != "" {
		return v, true
	}
	if v, okv := p.Secrets[key]; okv && strings.TrimSpace(v) != "" {
		return v, true
	}
	return "", false
}

// noteLiveSecret registers a value read from the live process environment so
// Redact treats it like any file-sourced secret. A live value is not in Env or
// Secrets, so without this it would be outside the redaction set.
func (p *Profile) noteLiveSecret(v string) {
	v = strings.TrimSpace(v)
	if len(v) < 6 {
		return
	}
	for _, existing := range p.liveSecrets {
		if existing == v {
			return
		}
	}
	p.liveSecrets = append(p.liveSecrets, v)
}

// Path joins a profile-relative path.
func (p *Profile) Path(rel string) string { return filepath.Join(p.Dir, rel) }

// Exists reports whether a profile-relative path exists.
func (p *Profile) Exists(rel string) bool {
	_, err := os.Stat(p.Path(rel))
	return err == nil
}

// DirExists reports whether a profile-relative path exists and is a directory.
func (p *Profile) DirExists(rel string) bool {
	info, err := os.Stat(p.Path(rel))
	return err == nil && info.IsDir()
}

// secretKeySegments mark a key whose value must never be printed.
var secretKeySegments = map[string]bool{
	"TOKEN": true, "SECRET": true, "SECRETS": true, "PASSWORD": true,
	"PASSWD": true, "PASS": true, "KEY": true, "APIKEY": true,
	"CREDENTIAL": true, "CREDENTIALS": true, "PAT": true, "AUTH": true,
	"SIGNATURE": true, "PRIVATE": true,
}

// isSecretKey reports whether a key's value is a secret. Matching is by
// underscore-separated segment so PATH does not match PAT, and a URL key like
// CL_BRAIN_API stays printable — over-redacting a daemon URL would hide the
// very detail the user needs to fix it.
func isSecretKey(key string) bool {
	for _, seg := range strings.Split(strings.ToUpper(key), "_") {
		if secretKeySegments[seg] {
			return true
		}
	}
	return false
}

// secretValues returns the values held in memory that must never appear in
// output: everything from the secrets env file, plus secret-keyed values from
// the profile env file.
func (p *Profile) secretValues() []string {
	var vals []string
	add := func(m map[string]string, secretKeysOnly bool) {
		for k, v := range m {
			if secretKeysOnly && !isSecretKey(k) {
				continue
			}
			v = strings.TrimSpace(v)
			// Very short values are not secrets and scrubbing them would
			// mangle unrelated text (e.g. a value of "1").
			if len(v) >= 6 {
				vals = append(vals, v)
			}
		}
	}
	add(p.Env, true)
	// The secrets env file holds nothing but secrets.
	add(p.Secrets, false)
	// Values read from the live environment are secrets by the same rule.
	vals = append(vals, p.liveSecrets...)
	// Longest first, so a value that contains another is scrubbed whole.
	sort.Slice(vals, func(i, j int) bool { return len(vals[i]) > len(vals[j]) })
	return vals
}

// Redact replaces any known secret value in s with a placeholder. This is the
// last line of defence: even if a subprocess echoes a token back at us, it
// cannot reach the user's terminal, a log, or the JSON output.
func (p *Profile) Redact(s string) string {
	if s == "" {
		return s
	}
	for _, v := range p.secretValues() {
		if strings.Contains(s, v) {
			s = strings.ReplaceAll(s, v, "[redacted]")
		}
	}
	return s
}

// LoadProfile reads a profile directory into a Profile. It never fails on a
// missing or unreadable env file — absence is something the checks report.
func LoadProfile(name, dir string, newRunner func(dir string, env map[string]string) CmdRunner) *Profile {
	p := &Profile{
		Name:    name,
		Dir:     dir,
		Env:     map[string]string{},
		Example: map[string]string{},
	}

	if env, err := ReadEnvFile(filepath.Join(dir, EnvFileName)); err == nil {
		p.Env = env
		p.HasEnvFile = true
	} else if !os.IsNotExist(err) {
		// Present but unreadable still counts as present.
		p.HasEnvFile = true
	}

	if ex, err := ReadEnvFile(filepath.Join(dir, ExampleEnvFileName)); err == nil {
		p.Example = ex
		p.HasExample = true
	}

	secretsPath := filepath.Join(dir, SecretsEnvFileName)
	if _, err := os.Stat(secretsPath); err == nil {
		secrets, rerr := ReadEnvFile(secretsPath)
		switch {
		case rerr == nil:
			p.Secrets = secrets
		case errors.Is(rerr, errUnreadable):
			p.SecretsNote = rerr.Error()
		default:
			p.SecretsNote = "secrets env file could not be read"
		}
	}

	if newRunner == nil {
		newRunner = ExecRunner
	}
	// Token values are handed to subprocesses via their environment, never as
	// command-line arguments (argv is world-readable on many systems).
	p.newRunner = newRunner
	p.Run = newRunner(dir, p.subprocessEnv())
	return p
}

// subprocessEnv is the env overlay handed to child processes: the profile env
// file plus whatever the secrets file provided.
func (p *Profile) subprocessEnv() map[string]string {
	env := map[string]string{}
	for k, v := range p.Env {
		env[k] = v
	}
	for k, v := range p.Secrets {
		env[k] = v
	}
	env["WORKSPACE_HOME"] = p.Dir
	env["WORKSPACE_PROFILE"] = p.Name
	return env
}
