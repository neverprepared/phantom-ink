package main

import (
	"sort"

	"phantom-ink/brainbox"
)

// AgentJobSummary is one fanned job (hub tasks grouped by job_id) as shown in
// the Jobs panel's list. It is derived client-side by grouping recent tasks.
type AgentJobSummary struct {
	JobID       string         `json:"job_id"`
	AgentName   string         `json:"agent_name"`
	Description string         `json:"description"`
	Total       int            `json:"total"`
	ByStatus    map[string]int `json:"by_status"`
	CreatedAt   int64          `json:"created_at"` // newest task in the group (epoch ms)
}

// AgentJobDetail is one job's per-target tasks plus its status roll-up.
type AgentJobDetail struct {
	JobID   string                    `json:"job_id"`
	Summary brainbox.JobStatusSummary `json:"summary"`
	Tasks   []HubTask                 `json:"tasks"`
}

// SubmitAgentJob fans one agent spec across many machines (one autonomous task
// per target, all sharing a minted job_id). Fire-and-forget — returns the job
// id and per-target task ids immediately.
func (a *App) SubmitAgentJob(req brainbox.SubmitJobRequest) (brainbox.JobSubmitResult, error) {
	return a.client.SubmitJob(req)
}

// GetAgentJob rolls up one job's per-target tasks (normalized for the UI) — the
// "walk away, come back to results" read.
func (a *App) GetAgentJob(jobID string) (AgentJobDetail, error) {
	d, err := a.client.GetJob(jobID)
	if err != nil {
		return AgentJobDetail{}, err
	}
	return AgentJobDetail{
		JobID:   d.JobID,
		Summary: d.Summary,
		Tasks:   normalizeHubTasks(d.Tasks),
	}, nil
}

// ListAgentJobs lists recent jobs by grouping hub tasks on job_id, newest
// first. A "job" is a flat fan-out group; single-task jobs (job_id == task id)
// appear too so ad-hoc tasks are visible alongside fanned ones.
func (a *App) ListAgentJobs() ([]AgentJobSummary, error) {
	ts, err := a.client.ListTasks("", "")
	if err != nil {
		return nil, err
	}
	groups := map[string]*AgentJobSummary{}
	for _, t := range normalizeHubTasks(ts) {
		key := t.JobID
		if key == "" {
			key = t.ID
		}
		g, ok := groups[key]
		if !ok {
			g = &AgentJobSummary{JobID: key, AgentName: t.AgentName, Description: t.Description, ByStatus: map[string]int{}}
			groups[key] = g
		}
		g.Total++
		g.ByStatus[t.Status]++
		if t.CreatedAt > g.CreatedAt {
			g.CreatedAt = t.CreatedAt
		}
	}
	out := make([]AgentJobSummary, 0, len(groups))
	for _, g := range groups {
		out = append(out, *g)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt > out[j].CreatedAt })
	return out, nil
}
