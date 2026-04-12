package main

import (
	"context"
	"fmt"
	"os"
	"phantom-ink/brainbox"
	goruntime "runtime"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// App is the Wails-bound struct. All exported methods become callable from JS.
type App struct {
	ctx    context.Context
	config *Config
	db     *DB
	client *brainbox.Client
	sse    *brainbox.SSEListener
}

// NewApp creates a new App instance.
func NewApp() *App {
	return &App{}
}

// startup is called by Wails when the app starts.
func (a *App) startup(ctx context.Context) {
	a.ctx = ctx

	// Open database (single source of truth)
	db, err := OpenDB()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to open database: %v\n", err)
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
	})
	a.sse.Start()
}

// shutdown is called by Wails when the app closes.
func (a *App) shutdown(_ context.Context) {
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
	cfg := *a.config
	if cfg.APIKey != "" {
		cfg.APIKey = "••••••••"
	}
	if cfg.Theme == "" {
		cfg.Theme = "dark"
	}
	return cfg
}

// SetTheme saves the theme preference ("dark" or "light").
func (a *App) SetTheme(theme string) error {
	a.config.Theme = theme
	if a.db != nil {
		return a.db.SetSetting("theme", theme)
	}
	return nil
}

// SetConfig updates and persists app configuration.
func (a *App) SetConfig(baseURL, apiKey, workspacesRoot string) error {
	if apiKey == "••••••••" {
		apiKey = a.config.APIKey
	}
	a.config.BaseURL = baseURL
	a.config.APIKey = apiKey
	if workspacesRoot != "" {
		a.config.WorkspacesRoot = workspacesRoot
	}
	a.client.Update(baseURL, apiKey)
	if a.sse != nil {
		a.sse.Restart()
	}
	if a.db != nil {
		if err := a.db.SetSetting("base_url", baseURL); err != nil {
			fmt.Fprintf(os.Stderr, "warning: failed to save setting %q: %v\n", "base_url", err)
		}
		if err := a.db.SetSetting("api_key", apiKey); err != nil {
			fmt.Fprintf(os.Stderr, "warning: failed to save setting %q: %v\n", "api_key", err)
		}
		if workspacesRoot != "" {
			if err := a.db.SetSetting("workspaces_root", workspacesRoot); err != nil {
				fmt.Fprintf(os.Stderr, "warning: failed to save setting %q: %v\n", "workspaces_root", err)
			}
		}
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

// BrowseFolder opens a native folder selection dialog and returns the path.
func (a *App) BrowseFolder() (string, error) {
	return runtime.OpenDirectoryDialog(a.ctx, runtime.OpenDialogOptions{
		Title: "Select folder to mount",
	})
}
