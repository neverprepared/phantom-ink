package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"phantom-ink/brainbox"
	"phantom-ink/internal/outbox"
	goruntime "runtime"
	"strings"
	"sync"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// App is the Wails-bound struct. All exported methods become callable from JS.
type App struct {
	ctx        context.Context
	mu         sync.RWMutex // protects config
	config     *Config
	db         *DB
	client     *brainbox.Client
	sse        *brainbox.SSEListener
	worker           *worker
	workerStop       context.CancelFunc
	scheduler        *scheduler
	schedulerStop    context.CancelFunc
	collectScheduler *collectScheduler
	collectStop      context.CancelFunc
	automations      *AutomationEngine
	automationsStop  context.CancelFunc
	localRunner      *localRunner
	localRunnerStop  context.CancelFunc
	outbox           *outbox.Outbox
	outboxStop       context.CancelFunc
}

// NewApp creates a new App instance.
func NewApp() *App {
	return &App{}
}

// errNoDB is the single error returned when a DB-backed method is called before
// the store is ready. Previously the same condition returned three different
// messages ("database not initialized" / "database not available" /
// "db unavailable"); this is the one sentinel, so callers/tests can errors.Is
// against it.
var errNoDB = errors.New("database not initialized")

// requireDB reports whether the store is ready, returning errNoDB otherwise —
// the guard for error-only DB methods (if err := a.requireDB(); err != nil {
// return err }). Methods that must also return a value use errNoDB directly:
// return X{}, errNoDB.
func (a *App) requireDB() error {
	if a.db == nil {
		return errNoDB
	}
	return nil
}

// startup is called by Wails when the app starts.
func (a *App) startup(ctx context.Context) {
	a.ctx = ctx

	// Open database (single source of truth)
	db, err := OpenDB()
	if err != nil {
		logErr("Failed to open database: %v", err)
		// Emit a startup-error event once the window is ready so the user
		// sees a visible notification rather than silent degraded behaviour.
		go func() {
			time.Sleep(500 * time.Millisecond)
			runtime.EventsEmit(ctx, "app:startup-error",
				fmt.Sprintf("Database failed to open: %v. Most features will be unavailable.", err))
		}()
	}
	a.db = db

	// Seed integration defaults for any missing entries
	if a.db != nil {
		for _, def := range knownServices {
			if _, ok := a.db.GetIntegration(def.Name); !ok {
				if err := a.db.UpsertIntegration(IntegrationRow{
					Name: def.Name, Enabled: false, LocalURL: def.DefaultURL,
				}); err != nil {
					fmt.Fprintf(os.Stderr, "warning: failed to seed integration %q: %v\n", def.Name, err)
				}
			}
		}
	}

	// Load config from DB
	a.config = loadConfigFromDB(a.db)

	a.client = brainbox.NewClient(a.config.BaseURL, a.config.APIKey)
	a.sse = brainbox.NewSSEListener(a.client, func(event string) {
		runtime.EventsEmit(ctx, "brainbox:event", event)

		// Inspect the wrapper once. brainbox /api/events broadcasts every
		// listener as JSON; agent.event wraps {"event":"agent.event","data":{...envelope}}
		// while webhook events use {"action":"webhook.trigger",...}.
		var probe struct {
			Event   string                 `json:"event"`
			Data    map[string]interface{} `json:"data"`
			Action  string                 `json:"action"`
			Key     string                 `json:"key"`
			Payload map[string]interface{} `json:"payload"`
		}
		if json.Unmarshal([]byte(event), &probe) != nil {
			return
		}

		// Fan-out agent.event so the Stream panel gets typed envelope deltas.
		if probe.Event == "agent.event" && probe.Data != nil {
			runtime.EventsEmit(ctx, "agent:event", probe.Data)
		}

		// Route webhook.trigger events to the automation engine.
		if a.automations != nil && probe.Action == "webhook.trigger" && probe.Key != "" {
			a.automations.Emit(AutomationEvent{
				Type:           "webhook",
				WebhookKey:     probe.Key,
				WebhookPayload: probe.Payload,
			})
		}
	})
	a.sse.Start()

	// Seed the agents catalog in the background — version probes can block
	// briefly and we don't want to delay window paint.
	go func() {
		if _, err := a.RescanAgents(); err != nil {
			logErr("warning: initial agent rescan failed: %v", err)
		}
	}()

	// Start the agent-event-bus outbox. Producers (queue, loop executor) append
	// envelopes; the flush loop ships batches to brainbox /api/agent_events with
	// exponential backoff. Brainbox dedups by envelope id.
	if a.db != nil {
		outboxCtx, outboxCancel := context.WithCancel(ctx)
		a.outboxStop = outboxCancel
		a.outbox = outbox.New(a.db.Conn(), func(_ context.Context, batch []outbox.Envelope) error {
			raws := make([]json.RawMessage, len(batch))
			for i, env := range batch {
				b, err := json.Marshal(env)
				if err != nil {
					return err
				}
				raws[i] = b
			}
			_, err := a.client.IngestAgentEvents(raws)
			return err
		}, outbox.Options{})
		a.outbox.Start(outboxCtx)
	}

	// Start the task queue worker. Stopped during shutdown via workerStop.
	if a.db != nil {
		workerCtx, cancel := context.WithCancel(ctx)
		a.workerStop = cancel
		a.worker = newWorker(a)
		a.worker.Start(workerCtx)

		// Cron scheduler — enqueues tasks for due schedules.
		schedCtx, schedCancel := context.WithCancel(ctx)
		a.schedulerStop = schedCancel
		a.scheduler = newScheduler(a)
		a.scheduler.Start(schedCtx)

		// Collect scheduler — runs data collection jobs and stores entries.
		collectCtx, collectCancel := context.WithCancel(ctx)
		a.collectStop = collectCancel
		a.collectScheduler = newCollectScheduler(a)
		// One-time heal for widget-owned collect jobs that accumulated from the
		// auto-registration race and dashboard rebuilds:
		//  1. collapse same-widget duplicates (keep the oldest), then
		//  2. drop orphans whose owning widget is gone from the saved layout.
		if n, err := a.db.DedupeWidgetJobs(); err != nil {
			fmt.Fprintf(os.Stderr, "collect: dedupe widget jobs: %v\n", err)
		} else if n > 0 {
			fmt.Fprintf(os.Stderr, "collect: removed %d duplicate widget-owned job(s)\n", n)
		}
		if n, err := a.db.PruneOrphanWidgetJobs(); err != nil {
			fmt.Fprintf(os.Stderr, "collect: prune orphan widget jobs: %v\n", err)
		} else if n > 0 {
			fmt.Fprintf(os.Stderr, "collect: removed %d orphan widget-owned job(s)\n", n)
		}
		a.collectScheduler.Start(collectCtx)

		// Automation engine — evaluates event-driven rules and fires actions.
		automationCtx, automationCancel := context.WithCancel(ctx)
		a.automationsStop = automationCancel
		a.automations = newAutomationEngine(a)
		a.automations.Start(automationCtx)
	}

	// Start local runner if enabled.
	if a.db != nil {
		if enabled := a.db.GetSetting(settingLocalRunnerEnabled, ""); enabled == "true" {
			name := a.db.GetSetting(settingLocalRunnerName, "local-mac")
			machineID := a.db.GetSetting(settingLocalRunnerMachineID, "")
			runnerCtx, runnerCancel := context.WithCancel(ctx)
			a.localRunnerStop = runnerCancel
			a.localRunner = newLocalRunner(a.client, name, machineID)
			a.localRunner.Start(runnerCtx)
		}
	}
}

// shutdown is called by Wails when the app closes.
func (a *App) shutdown(_ context.Context) {
	// Stop the queue worker and scheduler first so they don't grab work
	// while the DB is being closed. Wait for both goroutines to exit.
	if a.workerStop != nil {
		a.workerStop()
	}
	if a.schedulerStop != nil {
		a.schedulerStop()
	}
	if a.collectStop != nil {
		a.collectStop()
	}
	if a.automationsStop != nil {
		a.automationsStop()
	}
	if a.localRunnerStop != nil {
		a.localRunnerStop()
	}
	if a.outboxStop != nil {
		a.outboxStop()
	}
	if a.outbox != nil {
		a.outbox.Stop()
	}
	if a.worker != nil {
		a.worker.Wait()
	}
	if a.scheduler != nil {
		a.scheduler.Wait()
	}
	if a.collectScheduler != nil {
		a.collectScheduler.Wait()
	}
	if a.localRunner != nil {
		a.localRunner.Wait()
	}
	if a.sse != nil {
		a.sse.Stop()
	}
	if a.db != nil {
		if err := a.db.Close(); err != nil {
			fmt.Fprintf(os.Stderr, "warning: failed to close database: %v\n", err)
		}
	}
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

// GetConfig returns the current app configuration (API key masked).
func (a *App) GetConfig() Config {
	a.mu.RLock()
	cfg := *a.config
	a.mu.RUnlock()
	if cfg.APIKey != "" {
		cfg.APIKey = "••••••••"
	}
	// Apply defaults for fields that must never be empty, even if a blank
	// value was explicitly stored in the database by a previous run.
	if cfg.Theme == "" {
		cfg.Theme = "dark"
	}
	if cfg.BaseURL == "" {
		cfg.BaseURL = "http://127.0.0.1:9999"
	}
	if cfg.WorkspacesRoot == "" {
		if root := a.db.GetWorkspacesRoot(); root != "" {
			cfg.WorkspacesRoot = root
		} else {
			cfg.WorkspacesRoot = defaultWorkspacesRoot()
		}
	}
	return cfg
}

// SetTheme saves the theme preference ("dark" or "light").
func (a *App) SetTheme(theme string) error {
	a.mu.Lock()
	a.config.Theme = theme
	a.mu.Unlock()
	if a.db != nil {
		return a.db.SetSetting(settingTheme, theme)
	}
	return nil
}

// SetConfig updates and persists app configuration.
func (a *App) SetConfig(baseURL, apiKey, workspacesRoot string) error {
	a.mu.Lock()
	// Strip any leading/trailing whitespace — paste-from-`cat …` includes
	// a trailing newline that quietly breaks X-API-Key auth.
	baseURL = strings.TrimSpace(baseURL)
	apiKey = strings.TrimSpace(apiKey)
	workspacesRoot = strings.TrimSpace(workspacesRoot)
	if apiKey == "••••••••" {
		apiKey = a.config.APIKey
	}
	// Reject a blank baseURL rather than storing a value that would break
	// the brainbox client on the next startup.
	if baseURL == "" {
		baseURL = "http://127.0.0.1:9999"
	}
	a.config.BaseURL = baseURL
	a.config.APIKey = apiKey
	if workspacesRoot != "" {
		a.config.WorkspacesRoot = workspacesRoot
	}
	a.mu.Unlock()

	a.client.Update(baseURL, apiKey)
	if a.sse != nil {
		a.sse.Restart()
	}
	if a.db != nil {
		if err := a.db.SetSetting(settingBaseURL, baseURL); err != nil {
			return fmt.Errorf("save base URL: %w", err)
		}
		if err := a.db.SetSetting(settingAPIKey, apiKey); err != nil {
			return fmt.Errorf("save API key: %w", err)
		}
		if workspacesRoot != "" {
			if err := a.db.SetSetting(settingWorkspacesRoot, workspacesRoot); err != nil {
				return fmt.Errorf("save workspaces root: %w", err)
			}
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// Platform
// ---------------------------------------------------------------------------

// RestartBrainboxAPI restarts the brainbox API and reconnects the SSE listener.
// It tries Docker first (container with the brainbox-api compose label), then
// falls back to the Homebrew-installed daemon CLI.
func (a *App) RestartBrainboxAPI() error {
	if err := a.restartViaDocker(); err == nil {
		return a.waitAndReconnect()
	}
	if err := a.restartViaDaemon(); err != nil {
		return err
	}
	return a.waitAndReconnect()
}

func (a *App) restartViaDocker() error {
	out, err := exec.Command("docker", "ps",
		"--filter", "label=com.docker.compose.service=brainbox-api",
		"-q").Output()
	if err != nil {
		return err
	}
	containerID := strings.TrimSpace(string(out))
	if containerID == "" {
		return fmt.Errorf("no brainbox-api container running")
	}
	cmd := exec.Command("docker", "restart", containerID)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("%s: %w", strings.TrimSpace(string(out)), err)
	}
	return nil
}

func (a *App) restartViaDaemon() error {
	if _, err := exec.Command("pgrep", "-f", "python.*-m brainbox api").Output(); err != nil {
		return fmt.Errorf("no brainbox daemon process found")
	}
	projectDir := a.findBrainboxProject()
	if projectDir == "" {
		return fmt.Errorf("could not locate brainbox project (none of cwd ancestors, workspaces, or $HOME contained brainbox/pyproject.toml)")
	}
	cmd := exec.Command("uv", "run", "--directory", projectDir, "python", "-m", "brainbox", "restart")
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("daemon restart failed: %s: %w", strings.TrimSpace(string(out)), err)
	}
	return nil
}

// findBrainboxProject locates the brainbox Python project (the directory
// containing pyproject.toml that declares name = "brainbox"). Tries, in order:
//   1. The running daemon's cwd and its ancestors
//   2. <WorkspacesRoot>/<profile>/code/phantom-ink/brainbox for every profile
//   3. $HOME/workspaces/profiles/*/code/phantom-ink/brainbox
//   4. $HOME/code/phantom-ink/brainbox
// Returns "" if none look right. Existence + a pyproject.toml that names
// the project "brainbox" are both required.
func (a *App) findBrainboxProject() string {
	candidates := []string{}

	if pgrepOut, err := exec.Command("pgrep", "-f", "python.*-m brainbox api").Output(); err == nil {
		pid := strings.TrimSpace(strings.SplitN(string(pgrepOut), "\n", 2)[0])
		if pid != "" {
			if lsofOut, err := exec.Command("lsof", "-p", pid, "-d", "cwd", "-Fn").Output(); err == nil {
				for _, line := range strings.Split(string(lsofOut), "\n") {
					if strings.HasPrefix(line, "n") {
						candidates = append(candidates, strings.TrimPrefix(line, "n"))
						break
					}
				}
			}
		}
	}

	a.mu.RLock()
	wsRoot := a.config.WorkspacesRoot
	a.mu.RUnlock()
	if wsRoot != "" {
		if entries, err := os.ReadDir(wsRoot); err == nil {
			for _, e := range entries {
				if e.IsDir() {
					candidates = append(candidates, filepath.Join(wsRoot, e.Name(), "code", "phantom-ink", "brainbox"))
				}
			}
		}
	}

	home := os.Getenv("HOME")
	if home != "" {
		profilesDir := filepath.Join(home, "workspaces", "profiles")
		if entries, err := os.ReadDir(profilesDir); err == nil {
			for _, e := range entries {
				if e.IsDir() {
					candidates = append(candidates, filepath.Join(profilesDir, e.Name(), "code", "phantom-ink", "brainbox"))
				}
			}
		}
		candidates = append(candidates, filepath.Join(home, "code", "phantom-ink", "brainbox"))
	}

	for _, c := range candidates {
		if root := walkForBrainboxRoot(c); root != "" {
			return root
		}
	}
	return ""
}

// walkForBrainboxRoot returns the deepest dir at-or-above `start` that contains
// a pyproject.toml declaring name = "brainbox". Empty string if none.
func walkForBrainboxRoot(start string) string {
	dir := start
	for i := 0; i < 8; i++ {  // bounded ascent
		py := filepath.Join(dir, "pyproject.toml")
		if data, err := os.ReadFile(py); err == nil {
			if strings.Contains(string(data), `name = "brainbox"`) {
				return dir
			}
		}
		parent := filepath.Dir(dir)
		if parent == dir { // root
			return ""
		}
		dir = parent
	}
	return ""
}

func (a *App) waitAndReconnect() error {
	// Derive the host:port to probe from the configured BaseURL so remote
	// deployments (non-localhost, non-9999) reconnect correctly.
	a.mu.RLock()
	baseURL := a.config.BaseURL
	a.mu.RUnlock()

	addr := "localhost:9999" // safe default
	if u, err := url.Parse(baseURL); err == nil && u.Hostname() != "" {
		h := u.Hostname()
		p := u.Port()
		if p == "" {
			if u.Scheme == "https" {
				p = "443"
			} else {
				p = "80"
			}
		}
		addr = net.JoinHostPort(h, p)
	}

	for i := 0; i < 30; i++ {
		time.Sleep(1 * time.Second)
		conn, err := net.DialTimeout("tcp", addr, 1*time.Second)
		if err == nil {
			conn.Close()
			break
		}
	}
	if a.sse != nil {
		a.sse.Restart()
	}
	return nil
}

// ---------------------------------------------------------------------------
// Platform
// ---------------------------------------------------------------------------

// GetPlatform returns the OS: "darwin", "windows", or "linux".
func (a *App) GetPlatform() string {
	return goruntime.GOOS
}

// GetRegistrySettings returns the stored registry credentials.
func (a *App) GetRegistrySettings() map[string]string {
	if a.db == nil {
		return map[string]string{}
	}
	return map[string]string{
		"username": a.db.GetSetting(settingRegistryUsername, ""),
		"password": a.db.GetSetting(settingRegistryPassword, ""),
	}
}

// SetRegistrySettings persists registry credentials.
func (a *App) SetRegistrySettings(username, password string) error {
	if a.db == nil {
		return nil
	}
	if err := a.db.SetSetting(settingRegistryUsername, username); err != nil {
		return err
	}
	return a.db.SetSetting(settingRegistryPassword, password)
}

// GetOTLPHost returns the configured Data Prepper / OTLP host.
func (a *App) GetOTLPHost() string {
	if a.db == nil {
		return ""
	}
	return a.db.GetSetting(settingOTLPHost, "")
}

// SetOTLPHost persists the Data Prepper / OTLP host.
func (a *App) SetOTLPHost(host string) error {
	if a.db == nil {
		return nil
	}
	return a.db.SetSetting(settingOTLPHost, host)
}

// BrowseFolder opens a native folder selection dialog and returns the path.
func (a *App) BrowseFolder() (string, error) {
	return runtime.OpenDirectoryDialog(a.ctx, runtime.OpenDialogOptions{
		Title: "Select folder to mount",
	})
}
