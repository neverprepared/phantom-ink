package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

// ── Types ──────────────────────────────────────────────────────────────────

// AutomationRule defines a trigger → action pair.
type AutomationRule struct {
	ID              string `json:"id"`
	Profile         string `json:"profile"`
	Name            string `json:"name"`
	Description     string `json:"description"`
	Enabled         bool   `json:"enabled"`
	TriggerType     string `json:"trigger_type"`
	TriggerConfig   string `json:"trigger_config"`
	ActionType      string `json:"action_type"`
	ActionConfig    string `json:"action_config"`
	CreatedAt       int64  `json:"created_at"`
	LastTriggeredAt *int64 `json:"last_triggered_at"`
	TriggerCount    int    `json:"trigger_count"`
}

// AutomationEvent is emitted by various parts of the app and evaluated against rules.
type AutomationEvent struct {
	Type           string         // "entry_created" | "entry_status_change" | "job_complete" | "webhook"
	Profile        string
	Entry          *CollectedEntry        // populated for entry events
	Job            *CollectJob            // populated for job_complete
	WebhookKey     string                 // populated for webhook events
	WebhookPayload map[string]interface{} // arbitrary JSON body from the webhook caller
}

// ── Engine ─────────────────────────────────────────────────────────────────

const maxConcurrentActions = 10

// AutomationEngine listens on an event bus, evaluates rules, and fires actions.
type AutomationEngine struct {
	app *App
	bus chan AutomationEvent
	sem chan struct{} // bounds concurrent fireAction goroutines
}

func newAutomationEngine(app *App) *AutomationEngine {
	return &AutomationEngine{
		app: app,
		bus: make(chan AutomationEvent, 128),
		sem: make(chan struct{}, maxConcurrentActions),
	}
}

// Emit sends an event to the engine. Non-blocking — drops if bus is full.
func (e *AutomationEngine) Emit(evt AutomationEvent) {
	select {
	case e.bus <- evt:
	default:
	}
}

// Start begins processing events in a background goroutine.
func (e *AutomationEngine) Start(ctx context.Context) {
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case evt := <-e.bus:
				e.process(evt)
			}
		}
	}()
}

func (e *AutomationEngine) process(evt AutomationEvent) {
	if e.app.db == nil {
		return
	}
	rules, err := e.app.db.ListEnabledAutomationRules(evt.Profile)
	if err != nil {
		logErr("automation: list rules: %v", err)
		return
	}
	for _, rule := range rules {
		r := rule
		if !matchesTrigger(r, evt) {
			continue
		}
		if err := e.app.db.RecordAutomationTrigger(r.ID); err != nil {
			logErr("automation: record trigger for rule %s: %v", r.ID, err)
		}
		select {
		case e.sem <- struct{}{}:
			go func() {
				defer func() { <-e.sem }()
				e.fireAction(r, evt)
			}()
		default:
			logErr("automation: action pool full, dropping rule %s (%s)", r.ID, r.Name)
		}
	}
}

// ── Rule matching ──────────────────────────────────────────────────────────

type triggerConfig struct {
	Kind   string   `json:"kind"`
	Tags   []string `json:"tags"`
	Status string   `json:"status"`
	JobID  string   `json:"job_id"`
	Key    string   `json:"key"` // webhook key
}

func matchesTrigger(rule AutomationRule, evt AutomationEvent) bool {
	if rule.TriggerType != evt.Type {
		return false
	}
	var cfg triggerConfig
	if err := json.Unmarshal([]byte(rule.TriggerConfig), &cfg); err != nil {
		logErr("automation: rule %s has invalid trigger_config JSON: %v", rule.ID, err)
		return false
	}

	switch rule.TriggerType {
	case "webhook":
		if cfg.Key == "" || cfg.Key != evt.WebhookKey {
			return false
		}
		// Webhook rules are profile-agnostic by default (key is the discriminator).
		return true
	}

	if evt.Entry != nil {
		if cfg.Kind != "" && evt.Entry.Kind != cfg.Kind {
			return false
		}
		if cfg.Status != "" && evt.Entry.Status != cfg.Status {
			return false
		}
		for _, tag := range cfg.Tags {
			if !containsStr(evt.Entry.Tags, tag) {
				return false
			}
		}
	}
	if evt.Job != nil && cfg.JobID != "" && evt.Job.ID != cfg.JobID {
		return false
	}
	// Empty profile = global rule applies to all profiles.
	if rule.Profile != "" && rule.Profile != evt.Profile {
		return false
	}
	return true
}

func containsStr(slice []string, s string) bool {
	for _, v := range slice {
		if v == s {
			return true
		}
	}
	return false
}

// ── Action dispatch ────────────────────────────────────────────────────────

func (e *AutomationEngine) fireAction(rule AutomationRule, evt AutomationEvent) {
	var cfg map[string]string
	_ = json.Unmarshal([]byte(rule.ActionConfig), &cfg)

	switch rule.ActionType {
	case "fire_job":
		jobID := cfg["job_id"]
		if jobID == "" {
			return
		}
		job, ok := e.app.db.GetCollectJob(jobID)
		if !ok || !job.Enabled {
			return
		}
		now := time.Now().UnixMilli()
		entries, runErr := e.app.dispatchCollectJob(job)
		errStr := ""
		if runErr != nil {
			errStr = runErr.Error()
		}
		_ = e.app.db.markCollectJobRun(job.ID, now, errStr)
		for _, entry := range entries {
			_ = e.app.db.UpsertCollectedEntry(entry)
			e.app.emitCollectedEntryEnvelope(job, entry)
		}
		if len(entries) > 0 {
			e.app.emitCollectUpdate(evt.Profile)
		}

	case "run_loop":
		loopID := cfg["loop_id"]
		if loopID == "" {
			return
		}
		_, _ = e.app.RunSequence(loopID, renderTemplate(cfg["input"], evt), "")

	case "notify":
		title := renderTemplate(cfg["title"], evt)
		body := renderTemplate(cfg["body"], evt)
		if title == "" {
			title = "phantom-ink"
		}
		sendOSNotification(title, body)
	}
}

// ── Template rendering ─────────────────────────────────────────────────────

var metaKeyRe     = regexp.MustCompile(`\{metadata\.([^}]+)\}`)
var payloadKeyRe  = regexp.MustCompile(`\{payload\.([^}]+)\}`)

func renderTemplate(tmpl string, evt AutomationEvent) string {
	if tmpl == "" {
		return ""
	}
	title, description, tagsStr := "", "", ""
	meta := map[string]interface{}{}

	if evt.Entry != nil {
		title = evt.Entry.Title
		description = evt.Entry.Description
		tagsStr = strings.Join(evt.Entry.Tags, ", ")
		if evt.Entry.Metadata != nil {
			_ = json.Unmarshal(evt.Entry.Metadata, &meta)
		}
	} else if evt.Job != nil {
		title = evt.Job.Name
	}

	result := tmpl
	result = strings.ReplaceAll(result, "{title}", title)
	result = strings.ReplaceAll(result, "{description}", description)
	result = strings.ReplaceAll(result, "{tags}", tagsStr)
	result = metaKeyRe.ReplaceAllStringFunc(result, func(match string) string {
		key := metaKeyRe.FindStringSubmatch(match)[1]
		if val, ok := meta[key]; ok {
			return fmt.Sprintf("%v", val)
		}
		return ""
	})
	result = payloadKeyRe.ReplaceAllStringFunc(result, func(match string) string {
		key := payloadKeyRe.FindStringSubmatch(match)[1]
		if evt.WebhookPayload != nil {
			if val, ok := evt.WebhookPayload[key]; ok {
				return fmt.Sprintf("%v", val)
			}
		}
		return ""
	})
	return result
}

// ── Notifications ──────────────────────────────────────────────────────────

func sendOSNotification(title, body string) {
	script := fmt.Sprintf(`display notification %q with title %q`, body, title)
	_ = exec.Command("osascript", "-e", script).Run()
}
