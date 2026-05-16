package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"sync"
	"time"

	"github.com/robfig/cron/v3"
)

// cronParser is the shared expression parser. We accept standard 5-field
// (minute, hour, dom, month, dow) cron plus the @hourly/@daily/@weekly
// descriptors that robfig/cron supports.
var cronParser = cron.NewParser(
	cron.Minute | cron.Hour | cron.Dom | cron.Month | cron.Dow | cron.Descriptor,
)

// scheduler ticks every 30s, evaluates all enabled schedules, and enqueues a
// task for any whose next fire time has elapsed since the last recorded fire.
// 30s granularity keeps the worker cheap while staying responsive enough for
// minute-level cron expressions.
type scheduler struct {
	app      *App
	interval time.Duration
	stopOnce sync.Once
	stopped  chan struct{}
}

func newScheduler(a *App) *scheduler {
	return &scheduler{
		app:      a,
		interval: 30 * time.Second,
		stopped:  make(chan struct{}),
	}
}

func (s *scheduler) Start(ctx context.Context) {
	go func() {
		defer close(s.stopped)
		ticker := time.NewTicker(s.interval)
		defer ticker.Stop()
		// Tick immediately so a just-due schedule fires without a 30s wait.
		s.tick()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				s.tick()
			}
		}
	}()
}

func (s *scheduler) Wait() { <-s.stopped }

func (s *scheduler) tick() {
	if s.app.db == nil {
		return
	}
	schedules, err := s.app.db.ListSchedules("")
	if err != nil {
		fmt.Fprintf(os.Stderr, "warning: scheduler list: %v\n", err)
		return
	}
	now := time.Now().UTC()
	for _, sch := range schedules {
		if !sch.Enabled {
			continue
		}
		sched, err := cronParser.Parse(sch.CronExpr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "warning: schedule %s invalid cron %q: %v\n", sch.ID, sch.CronExpr, err)
			continue
		}
		// Anchor "since" to the last fire (or to created_at if never fired).
		anchor := parseTimeOr(sch.LastFiredAt, parseTimeOr(sch.CreatedAt, now.Add(-s.interval)))
		next := sched.Next(anchor)
		if next.After(now) {
			continue
		}
		// Due — enqueue and stamp the fire so we don't re-trigger next tick.
		if _, err := s.app.EnqueueTask(EnqueueTaskRequest{
			ChainID:  sch.ChainID,
			Input:    sch.Input,
			Cwd:      sch.Cwd,
			Trigger:  TriggerSchedule,
			Priority: 0,
		}); err != nil {
			fmt.Fprintf(os.Stderr, "warning: schedule %s enqueue: %v\n", sch.ID, err)
			continue
		}
		if err := s.app.db.MarkScheduleFired(sch.ID, now.Format(time.RFC3339)); err != nil {
			fmt.Fprintf(os.Stderr, "warning: schedule %s mark fired: %v\n", sch.ID, err)
		}
	}
}

func parseTimeOr(s string, fallback time.Time) time.Time {
	if s == "" {
		return fallback
	}
	t, err := time.Parse(time.RFC3339, s)
	if err != nil {
		return fallback
	}
	return t
}

func newScheduleID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return "sched-" + hex.EncodeToString(b[:])
}

// validateCronExpr returns nil if the expression parses. Used by SaveSchedule
// to reject bad input before it gets persisted and silently ignored later.
func validateCronExpr(expr string) error {
	_, err := cronParser.Parse(expr)
	return err
}
