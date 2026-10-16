package doctor

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// DockerSymlinkCheck guards the OrbStack migration failure: Docker Desktop used
// to provide /usr/local/bin/docker, and without it GUI/launchd apps (the runner
// app) have no docker on PATH, so session creation dies with exit 127 while the
// control plane still looks healthy.
type DockerSymlinkCheck struct {
	// homeDir resolves the operator's home directory, defaulting to
	// os.UserHomeDir. It is a field (rather than a bare call) so a test can
	// make the lookup fail without relying on unsetting $HOME, which does not
	// portably affect os.UserHomeDir on macOS.
	homeDir func() (string, error)
}

func (DockerSymlinkCheck) ID() string   { return "docker-symlink" }
func (DockerSymlinkCheck) Scope() Scope { return ScopeLocal }

func (c DockerSymlinkCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{
		ID:    c.ID(),
		Title: "docker on the GUI PATH",
		Scope: ScopeLocal,
	}
	_, code, err := ex.Shell(ctx, []string{"test", "-x", "/usr/local/bin/docker"})
	if err != nil {
		res.State = StateUnknown
		res.Detail = "could not test /usr/local/bin/docker: " + err.Error()
		return res
	}
	if code == 0 {
		res.State = StatePass
		res.Detail = "/usr/local/bin/docker is executable"
		return res
	}
	res.State = StateFail
	res.Detail = "/usr/local/bin/docker is missing; GUI and launchd apps have no docker on PATH"
	res.Remedy = &Remedy{
		Command: "ln -sf ~/.orbstack/bin/docker /usr/local/bin/docker",
		Confirm: "Symlink ~/.orbstack/bin/docker to /usr/local/bin/docker on this Mac.",
	}
	homeDir := c.homeDir
	if homeDir == nil {
		homeDir = os.UserHomeDir
	}
	home, err := homeDir()
	if err != nil {
		// The observation stands: the symlink is positively confirmed
		// missing, so this stays StateFail. What's unavailable is only the
		// ability to safely auto-run a fix, since the source path cannot be
		// resolved — never build an Exec argv from a path we couldn't get.
		res.Detail += "; could not resolve home directory for an auto-fix: " + err.Error()
		return res
	}
	res.Remedy.Exec = []string{"ln", "-sf", home + "/.orbstack/bin/docker", "/usr/local/bin/docker"}
	res.Remedy.AutoSafe = true
	return res
}

// AppDBCheck catches an app SQLite file that exists but was never initialised.
// A zero-byte ~/.phantom-ink.db has no tables at all, so every panel backed by
// local state fails in a way that looks like a query bug rather than a missing
// schema.
type AppDBCheck struct{ Path string }

func (AppDBCheck) ID() string   { return "app-db" }
func (AppDBCheck) Scope() Scope { return ScopeLocal }

func (c AppDBCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "app database initialised", Scope: ScopeLocal}
	if c.Path == "" || !filepath.IsAbs(c.Path) {
		// An empty or relative Path means we do not know where the real app
		// database lives — a relative path would stat whatever happens to sit
		// in the process's cwd, which can produce a plausible-looking
		// StatePass having never looked at the actual file. "Do not know" is
		// unknown, not pass.
		res.State = StateUnknown
		res.Detail = "no absolute database path to inspect"
		return res
	}
	out, _, err := ex.Shell(ctx, []string{"stat", "-f", "%z", c.Path})
	if err != nil {
		res.State = StateUnknown
		res.Detail = fmt.Sprintf("could not stat %s: %v", c.Path, err)
		return res
	}
	size, convErr := strconv.Atoi(strings.TrimSpace(out))
	if convErr != nil {
		res.State = StateUnknown
		res.Detail = fmt.Sprintf("could not read the size of %s", c.Path)
		return res
	}
	if size > 0 {
		res.State = StatePass
		res.Detail = fmt.Sprintf("%s is %d bytes", c.Path, size)
		return res
	}
	res.State = StateFail
	res.Detail = fmt.Sprintf("%s is empty — the schema was never created", c.Path)
	// Not auto-safe: the fix is an app restart so db_migrations.go runs, which
	// Doctor cannot perform as a shell command on itself.
	res.Remedy = &Remedy{
		Command:  "restart phantom-ink so db_migrations.go runs",
		AutoSafe: false,
	}
	return res
}

// DirenvApprovalCheck exists because .envrc approvals are per-profile. An
// unapproved profile does not fail loudly: the app resolves that profile's env
// from disk instead, so scheduled jobs run with the wrong environment and
// nothing says so.
type DirenvApprovalCheck struct{ ProfileDirs []string }

func (DirenvApprovalCheck) ID() string   { return "direnv-approval" }
func (DirenvApprovalCheck) Scope() Scope { return ScopeLocal }

func (c DirenvApprovalCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "direnv approvals per profile", Scope: ScopeLocal}
	if len(c.ProfileDirs) == 0 {
		// Zero profile directories means nothing was observed, not that zero
		// profiles are unapproved. Reporting StatePass here — as "all 0
		// profiles approved" — would be a confident green over an empty
		// observation, which is exactly the false-pass class this package
		// exists to prevent (a profile-scan failure must not read as success).
		res.State = StateUnknown
		res.Detail = "no profile directories to check"
		return res
	}
	var unapproved, unreadable []string
	for _, dir := range c.ProfileDirs {
		// direnv has no --profile flag and silently ignores one, so scope by cwd.
		// dir is passed as a shell argument ($1), never interpolated into the
		// script text, so a space or shell metacharacter in a profile path
		// can't break or hijack the command. "sh" before dir fills $0, which
		// sh -c requires before positional args begin.
		out, _, err := ex.Shell(ctx, []string{"sh", "-c", `cd "$1" && direnv status`, "sh", dir})
		if err != nil {
			unreadable = append(unreadable, dir)
			continue
		}
		allowed, ok := parseDirenvAllowed(out)
		if !ok {
			unreadable = append(unreadable, dir)
			continue
		}
		if !allowed {
			unapproved = append(unapproved, dir)
		}
	}
	switch {
	case len(unreadable) > 0:
		res.State = StateUnknown
		res.Detail = "could not read direnv status for: " + strings.Join(unreadable, ", ")
	case len(unapproved) > 0:
		res.State = StateWarn
		res.Detail = "unapproved .envrc in: " + strings.Join(unapproved, ", ") +
			" — these profiles resolve env from disk instead"
		// Exec fixes one directory per click: a single argv, so what the confirm
		// text promises is exactly what runs.
		res.Remedy = &Remedy{
			Command:  "direnv allow " + strings.Join(unapproved, " && direnv allow "),
			Exec:     []string{"direnv", "allow", unapproved[0]},
			AutoSafe: true,
			Confirm:  "Run direnv allow in " + unapproved[0] + ".",
		}
	default:
		res.State = StatePass
		res.Detail = fmt.Sprintf("all %d profiles approved", len(c.ProfileDirs))
	}
	return res
}

// GitHubTokenShadowCheck catches an exported GH_TOKEN or GITHUB_TOKEN
// shadowing a working keyring login. gh honors both, with GH_TOKEN taking
// precedence when both are set, so a dead token in either makes every call
// fail while a good credential sits idle — and the symptoms mislead in
// sequence, first as a 403 that reads like a missing scope, then as a 401.
//
// The subject is the OPERATOR'S shell, not this process. Executor.Shell runs a
// command with the app's own environment, and a packaged .app launched from
// Finder or launchd inherits none of the operator's exports — so reading
// printenv directly found nothing and passed, while the shell that spawns every
// agent session carried exactly the stale token this check exists to catch. It
// was meaningful under `wails dev`, which does inherit the terminal env, and
// meaningless in the shipped binary. So probe through a login shell, and say in
// the detail WHOSE environment was observed: "the environment" is the wording
// that turns a limitation into a false pass.
//
// This check never puts a token value in Detail. Reporting health must not
// become a way to leak a credential into a log or a screenshot.
type GitHubTokenShadowCheck struct {
	// LoginShell is the operator's login shell, defaulting to zsh (the macOS
	// default and this workspace's shell). A field so a test can name it
	// without depending on the host.
	LoginShell string
}

func (GitHubTokenShadowCheck) ID() string   { return "github-token-shadow" }
func (GitHubTokenShadowCheck) Scope() Scope { return ScopeLocal }

func (c GitHubTokenShadowCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "no GitHub token shadowing the keyring", Scope: ScopeLocal}
	// GH_TOKEN is probed first because gh prefers it over GITHUB_TOKEN when
	// both are set, so it is the variable actually in effect and the one
	// named below. GITHUB_TOKEN is still checked when GH_TOKEN is absent —
	// dropping either probe would let that variable's shadowing go
	// undetected.
	shell := c.LoginShell
	if shell == "" {
		shell = "zsh"
	}
	name := ""
	for _, n := range []string{"GH_TOKEN", "GITHUB_TOKEN"} {
		// -l so the operator's rc files run and their exports are in scope;
		// this is the environment gh and every spawned agent session will
		// see, which is the thing being asked about.
		_, code, err := ex.Shell(ctx, []string{shell, "-lc", "printenv " + n})
		if err != nil {
			// The login shell could not be run at all, so the operator's
			// environment was not observed. Unknown, naming that — never a
			// pass over an environment nobody looked at.
			res.State = StateUnknown
			res.Detail = "could not run " + shell + " -lc to read your login shell's environment: " + err.Error()
			return res
		}
		// The exit code is the whole signal, deliberately: printenv exits 0
		// only when the variable exists. The output is not inspected because
		// a login shell's rc files can print to stdout/stderr and Shell
		// merges both, so a non-empty body proves nothing — and the value
		// itself must never be read into a field regardless.
		if code == 0 {
			name = n
			break
		}
	}
	if name == "" {
		res.State = StatePass
		res.Detail = "no GH_TOKEN or GITHUB_TOKEN exported in your login shell (" + shell + " -l)"
		return res
	}
	status, _, err := ex.Shell(ctx, []string{"gh", "auth", "status"})
	if err != nil {
		res.State = StateUnknown
		res.Detail = name + " is exported in your login shell, but gh auth status could not be read"
		return res
	}
	if strings.Contains(status, "Active account: false") {
		res.State = StateWarn
		res.Detail = name + " is exported in your login shell and gh reports an idle keyring account" +
			" — the env token wins and may be dead"
		res.Remedy = &Remedy{
			Command:  "unset " + name + "   # or: env -u " + name + " gh ...",
			AutoSafe: false,
			Confirm:  "",
		}
		return res
	}
	res.State = StatePass
	res.Detail = name + " is exported in your login shell and gh reports an active account"
	return res
}

// PortShadowCheck catches a host process holding a port the platform needs. A
// launchd router on :9910 shadows the containerised one, and the app then talks
// to the wrong thing while everything appears to be up.
//
// Non-emptiness of lsof's output is NOT the signal. In the local-platform
// configuration the platform's own router publishes 9910 to the host
// ("0.0.0.0:9910->9910/tcp"), and a published container port appears in lsof
// like any other listener — so testing emptiness reports a hard fail, with a
// destructive launchctl remedy for a job that does not exist, on every
// correctly-working local install. What distinguishes the two cases is the
// COMMAND column: a docker-owned listener is the platform publishing its own
// port; anything else is a genuine shadow.
type PortShadowCheck struct{ Port string }

func (c PortShadowCheck) ID() string { return "port-shadow" }
func (PortShadowCheck) Scope() Scope { return ScopeLocal }

func (c PortShadowCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{
		ID:    c.ID(),
		Title: "port " + c.Port + " is free for the platform",
		Scope: ScopeLocal,
	}
	out, code, err := ex.Shell(ctx, []string{"lsof", "-nP", "-iTCP:" + c.Port, "-sTCP:LISTEN"})
	if err != nil {
		res.State = StateUnknown
		res.Detail = "could not list listeners on :" + c.Port + ": " + err.Error()
		return res
	}
	// lsof exits non-zero with empty output when nothing is listening.
	if code != 0 && strings.TrimSpace(out) == "" {
		res.State = StatePass
		res.Detail = "nothing is listening on :" + c.Port
		return res
	}
	if strings.TrimSpace(out) == "" {
		res.State = StateUnknown
		res.Detail = "lsof returned no output and no error for :" + c.Port
		return res
	}
	listeners := parseLsofCommands(out)
	if len(listeners) == 0 {
		// Output arrived but no data line could be read out of it. That is
		// "I could not tell", not "the port is clear" and not "something is
		// shadowing it" — do not guess which.
		res.State = StateUnknown
		res.Detail = "could not read the COMMAND column of lsof's output for :" + c.Port
		return res
	}
	var foreign, dockerOwned []string
	for _, cmd := range listeners {
		if isDockerListener(cmd) {
			dockerOwned = append(dockerOwned, cmd)
			continue
		}
		foreign = append(foreign, cmd)
	}
	if len(foreign) == 0 {
		res.State = StatePass
		res.Detail = "docker publishes :" + c.Port + " (" + strings.Join(dedupe(dockerOwned), ", ") +
			") — the platform owns the port, nothing is shadowing it"
		return res
	}
	res.State = StateFail
	res.Detail = "a host process is listening on :" + c.Port +
		" (" + strings.Join(dedupe(foreign), ", ") + "), shadowing the platform service"
	res.Remedy = &Remedy{
		Command:  "launchctl unload <the job holding :" + c.Port + ">",
		AutoSafe: false,
		Confirm:  "",
	}
	return res
}

// dockerListenerPrefixes are the COMMAND values a container runtime's port
// publisher appears under. lsof truncates COMMAND to nine characters by
// default, which is why these are prefixes and why "com.docke" is listed as
// well as "com.docker": the real process is com.docker.backend.
var dockerListenerPrefixes = []string{
	"docker",
	"com.docke",
	"com.docker",
	"orbstack",
	"orbstk",
	"orb",
	"vpnkit",
}

func isDockerListener(command string) bool {
	c := strings.ToLower(strings.TrimSpace(command))
	for _, p := range dockerListenerPrefixes {
		if strings.HasPrefix(c, p) {
			return true
		}
	}
	return false
}

// parseLsofCommands returns the COMMAND column of every data row in lsof's
// output, skipping the header. A row with a single field is not a data row —
// lsof prints nine columns — so it is ignored rather than read as a command,
// which keeps unparseable output distinguishable from a real listener.
func parseLsofCommands(out string) []string {
	var cmds []string
	for _, line := range strings.Split(out, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 || fields[0] == "COMMAND" {
			continue
		}
		cmds = append(cmds, fields[0])
	}
	return cmds
}

func dedupe(in []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(in))
	for _, s := range in {
		if seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	return out
}

// GatewayMCPPinCheck guards a pin that silently breaks the gateway: mcp 2.0
// dropped Server.list_tools, so an unpinned rebuild crashes the gateway on any
// tool listing.
//
// The pin lives inside the gateway CONTAINER image, not in a host venv, so this
// reads it with `docker exec <container> pip show mcp`. Verified on this host:
// the container is phantom-gateway-1 and reports mcp 1.29.1. A stopped or
// missing container makes docker exec fail, which correctly yields Unknown —
// "I could not tell", not "the pin is broken".
type GatewayMCPPinCheck struct{ Container string }

func (GatewayMCPPinCheck) ID() string   { return "gateway-mcp-pin" }
func (GatewayMCPPinCheck) Scope() Scope { return ScopeLocal }

func (c GatewayMCPPinCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "gateway mcp pinned below 2.0", Scope: ScopeLocal}
	out, _, err := ex.Shell(ctx, []string{"docker", "exec", c.Container, "pip", "show", "mcp"})
	if err != nil {
		res.State = StateUnknown
		res.Detail = "could not read the gateway venv's mcp version: " + err.Error()
		return res
	}
	version := ""
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "Version:") {
			version = strings.TrimSpace(strings.TrimPrefix(line, "Version:"))
			break
		}
	}
	if version == "" {
		res.State = StateUnknown
		res.Detail = "pip show mcp printed no Version line"
		return res
	}
	major := strings.SplitN(version, ".", 2)[0]
	n, convErr := strconv.Atoi(major)
	if convErr != nil {
		res.State = StateUnknown
		res.Detail = "could not parse the mcp version " + version
		return res
	}
	if n >= 2 {
		res.State = StateFail
		res.Detail = "mcp " + version + " is installed; 2.0 dropped Server.list_tools and crashes the gateway"
		res.Remedy = &Remedy{
			Command:  "docker exec " + c.Container + " pip install 'mcp<2'  # then rebuild the image",
			AutoSafe: false,
			Confirm:  "",
		}
		return res
	}
	res.State = StatePass
	res.Detail = "mcp " + version
	return res
}

// parseDirenvAllowed reads the "Found RC allowed <n>" line. "Found" describes the
// directory asked about; "Loaded" describes direnv's own cwd, so only the former
// answers the question. Returns ok=false when the line is absent, which the
// caller turns into unknown rather than a guess.
func parseDirenvAllowed(out string) (allowed, ok bool) {
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "Found RC allowed") {
			continue
		}
		fields := strings.Fields(line)
		n, err := strconv.Atoi(fields[len(fields)-1])
		if err != nil {
			return false, false
		}
		return n == 0, true
	}
	return false, false
}
