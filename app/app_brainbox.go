package main

import (
	"fmt"
	"phantom-ink/brainbox"
	"time"
)

// ---------------------------------------------------------------------------
// Sessions — pass-through to brainbox client
// ---------------------------------------------------------------------------

// GetSessions returns brainbox sessions, optionally filtered to a workspace
// profile. Pass "" to return all sessions across profiles.
func (a *App) GetSessions(workspace string) ([]brainbox.Session, error) {
	sessions, err := a.client.ListSessions()
	if err != nil {
		return nil, err
	}
	if workspace == "" {
		return sessions, nil
	}
	out := sessions[:0]
	for _, s := range sessions {
		if s.WorkspaceProfile == workspace {
			out = append(out, s)
		}
	}
	return out, nil
}

func (a *App) GetSessionHistory(limit, offset int) ([]brainbox.SessionHistoryEntry, error) {
	return a.client.GetSessionHistory(limit, offset)
}

func (a *App) CreateSession(req brainbox.CreateSessionRequest) (brainbox.SessionActionResponse, error) {
	// Forward the calling profile's env vars to the remote brainbox host so
	// secrets like CLAUDE_CODE_OAUTH_TOKEN reach the container.
	if req.WorkspaceHome != "" && len(req.Env) == 0 {
		req.Env = brainbox.ReadProfileEnv(req.WorkspaceHome)
	}
	// Inject the profile env decryption key so the container can decrypt .env.enc.
	// Also signal "image" delivery so runners skip the bundle-injection step —
	// credentials are already baked into the profile image.
	if req.WorkspaceProfile != "" && a.db != nil {
		if row, ok := a.db.GetProfileImage(req.WorkspaceProfile); ok && row.EnvKey != "" {
			if req.Env == nil {
				req.Env = make(map[string]string)
			}
			req.Env["PROFILE_ENV_KEY"] = row.EnvKey
			if req.Delivery == "" {
				req.Delivery = "image"
			}
		}
	}
	return a.client.CreateSession(req)
}

// PreviewDispatch reports where a session with the given backend/runner/tags
// would land. Used by the session-create modal to show "→ will run on X"
// before the user clicks create.
func (a *App) PreviewDispatch(req brainbox.DispatchPreviewRequest) (brainbox.DispatchPreview, error) {
	return a.client.PreviewDispatch(req)
}

func (a *App) StartSession(name string) (brainbox.SessionActionResponse, error) {
	return a.client.StartSession(name)
}

func (a *App) StopSession(name string) (brainbox.SessionActionResponse, error) {
	return a.client.StopSession(name)
}

func (a *App) DeleteSession(name string) (brainbox.SessionActionResponse, error) {
	return a.client.DeleteSession(name)
}

// ---------------------------------------------------------------------------
// Hub
// ---------------------------------------------------------------------------

func (a *App) GetHubState() (brainbox.HubState, error) {
	return a.client.GetHubState()
}

// ListHubTasks returns brainbox hub tasks. Renamed from ListTasks to avoid
// shadowing the new App.ListTasks which reads the local task queue.
func (a *App) ListHubTasks(status, workspaceProfile string) ([]brainbox.Task, error) {
	return a.client.ListTasks(status, workspaceProfile)
}

func (a *App) SubmitTask(req brainbox.SubmitTaskRequest) (brainbox.Task, error) {
	return a.client.SubmitTask(req)
}

// CancelHubTask cancels a brainbox hub task. Renamed from CancelTask to
// avoid shadowing the local-queue CancelTask binding.
func (a *App) CancelHubTask(taskID string) error {
	return a.client.CancelTask(taskID)
}

// GetTaskLineage returns all tasks belonging to a job tree (same job_id).
// Pass the root supervisor's task ID as jobID. The Timeline panel uses this
// to build the full tree for a selected job.
func (a *App) GetTaskLineage(jobID string) ([]brainbox.Task, error) {
	return a.client.ListTasksByJob(jobID)
}

// ListAgentRoles returns brainbox's multi-agent role catalog (developer,
// supervisor, worker, reviewer, …). Renamed from ListAgents to avoid
// shadowing the new App.ListAgents which lists detected CLI tools.
func (a *App) ListAgentRoles() ([]brainbox.AgentDefinition, error) {
	return a.client.ListAgents()
}

// GetAgentRole returns a single brainbox agent role definition.
func (a *App) GetAgentRole(name string) (brainbox.AgentDefinition, error) {
	return a.client.GetAgent(name)
}

func (a *App) CreateAgent(req brainbox.CreateAgentRequest) (brainbox.AgentDefinition, error) {
	return a.client.CreateAgent(req)
}

func (a *App) UpdateAgent(name string, req brainbox.UpdateAgentRequest) (brainbox.AgentDefinition, error) {
	return a.client.UpdateAgent(name, req)
}

func (a *App) DeleteAgent(name string) error {
	return a.client.DeleteAgent(name)
}

// LaunchTeam creates only a supervisor session.  The supervisor's task prompt
// describes all available agents in the category and how to spawn them on
// demand via the hub task API — no idle container sessions are pre-launched.
//
// Container agents (spawn_mode="container") are launched by the supervisor via
// POST $BRAINBOX_HUB_URL/api/hub/tasks when it has concrete work for them.
// Subagents (spawn_mode="subagent") are spawned inline using Claude Code's
// built-in Task tool.
func (a *App) LaunchTeam(category string, task string, llmProvider string, llmModel string, workspaceProfile string, workspaceHome string) (brainbox.SessionActionResponse, error) {
	agents, err := a.client.ListAgents()
	if err != nil {
		return brainbox.SessionActionResponse{}, err
	}

	teamID := fmt.Sprintf("team-%s-%d", category, time.Now().Unix()%100000)

	type agentInfo struct {
		name        string
		description string
		spawnMode   string
	}
	var teamAgents []agentInfo
	for _, ag := range agents {
		if ag.Category != category || ag.Name == "supervisor" {
			continue
		}
		teamAgents = append(teamAgents, agentInfo{
			name:        ag.Name,
			description: ag.Description,
			spawnMode:   ag.SpawnMode,
		})
	}

	supervisorTask := task + "\n\n## Team `" + teamID + "` — available agents\n\n"
	supervisorTask += "You are the supervisor. Break the goal into concrete subtasks and delegate them. " +
		"Do not do all the work yourself.\n\n"

	// Container agents section
	var containerAgents []agentInfo
	var subAgents []agentInfo
	for _, ag := range teamAgents {
		if ag.spawnMode == "subagent" {
			subAgents = append(subAgents, ag)
		} else {
			containerAgents = append(containerAgents, ag)
		}
	}

	if len(containerAgents) > 0 {
		supervisorTask += "### Container agents — spawn via hub task API\n\n"
		supervisorTask += "Submit a task to a container agent with:\n" +
			"```\ncurl -s -X POST $BRAINBOX_HUB_URL/api/hub/tasks \\\n" +
			"  -H 'Content-Type: application/json' \\\n" +
			"  -H \"Authorization: Bearer $BRAINBOX_TOKEN\" \\\n" +
			"  -d '{\"agent_name\": \"<name>\", \"description\": \"<task>\"}'\n```\n\n" +
			"Available agents:\n\n"
		for _, ag := range containerAgents {
			supervisorTask += fmt.Sprintf("- **%s**", ag.name)
			if ag.description != "" {
				supervisorTask += ": " + ag.description
			}
			supervisorTask += "\n"
		}
		supervisorTask += "\n"
	}

	if len(subAgents) > 0 {
		supervisorTask += "### Subagents — spawn inline with the Task tool\n\n"
		supervisorTask += "Use Claude Code's built-in Task tool to spawn these agents as sub-tasks:\n\n"
		for _, ag := range subAgents {
			supervisorTask += fmt.Sprintf("- **%s**", ag.name)
			if ag.description != "" {
				supervisorTask += ": " + ag.description
			}
			supervisorTask += "\n"
		}
		supervisorTask += "\n"
	}

	resp, err := a.client.CreateSession(brainbox.CreateSessionRequest{
		Name:             teamID + "-supervisor",
		Role:             "supervisor",
		LLMProvider:      llmProvider,
		LLMModel:         llmModel,
		Task:             supervisorTask,
		WorkspaceProfile: workspaceProfile,
		WorkspaceHome:    workspaceHome,
	})
	return resp, err
}

func (a *App) GetMessageLog() ([]brainbox.Message, error) {
	return a.client.GetMessageLog()
}

// ---------------------------------------------------------------------------
// Observability
// ---------------------------------------------------------------------------

func (a *App) GetLangfuseHealth() (brainbox.HealthStatus, error) {
	return a.client.GetLangfuseHealth()
}

func (a *App) GetContainerMetrics() ([]brainbox.ContainerMetrics, error) {
	return a.client.GetContainerMetrics()
}

func (a *App) GetMetricsHistory() ([]brainbox.MetricsSample, error) {
	return a.client.GetMetricsHistory()
}

func (a *App) GetSessionsMetricsHistory() (map[string][]brainbox.SessionMetricsSample, error) {
	return a.client.GetSessionsMetricsHistory()
}

func (a *App) GetSessionTraces(sessionName string, limit int) ([]brainbox.Trace, error) {
	return a.client.GetSessionTraces(sessionName, limit)
}

func (a *App) GetTraceDetail(traceID string) (brainbox.TraceDetail, error) {
	return a.client.GetTraceDetail(traceID)
}

// ListChannels returns all group chat channels.
func (a *App) ListChannels(workspaceProfile string) ([]brainbox.Channel, error) {
	return a.client.ListChannels(workspaceProfile)
}

// GetChannel returns a single channel by ID.
func (a *App) GetChannel(id string) (brainbox.Channel, error) {
	return a.client.GetChannel(id)
}

// CreateChannel creates a new group chat channel.
func (a *App) CreateChannel(req brainbox.CreateChannelRequest) (brainbox.Channel, error) {
	return a.client.CreateChannel(req)
}

// GetChannelMessages returns messages for a channel, optionally since a given message ID.
func (a *App) GetChannelMessages(id, sinceID string) ([]brainbox.ChannelMessage, error) {
	return a.client.GetChannelMessages(id, sinceID)
}

// PostChannelMessage posts a message to a channel.
func (a *App) PostChannelMessage(id string, req brainbox.PostChannelMessageRequest) (brainbox.ChannelMessage, error) {
	return a.client.PostChannelMessage(id, req)
}

// CompleteChannel signals that a channel discussion is complete.
func (a *App) CompleteChannel(id string, req brainbox.CompleteChannelRequest) (brainbox.Channel, error) {
	return a.client.CompleteChannel(id, req)
}

// DeleteChannel deletes a channel and all its messages.
func (a *App) DeleteChannel(id string) error {
	return a.client.DeleteChannel(id)
}

// AddChannelParticipant attaches a session (or other participant) to an
// existing conversation. The UI uses this to drop agents into live channels.
func (a *App) AddChannelParticipant(id string, req brainbox.ChannelParticipantRequest) (brainbox.Channel, error) {
	return a.client.AddChannelParticipant(id, req)
}

// RemoveChannelParticipant detaches a participant by name. Their past
// messages stay in the log.
func (a *App) RemoveChannelParticipant(id, name string) (brainbox.Channel, error) {
	return a.client.RemoveChannelParticipant(id, name)
}

// ListPlaybooks returns playbooks, optionally filtered by profile.
func (a *App) ListPlaybooks(profile string) ([]brainbox.Playbook, error) {
	return a.client.ListPlaybooks(profile)
}

// GetPlaybook returns a single playbook by ID.
func (a *App) GetPlaybook(id string) (brainbox.Playbook, error) {
	return a.client.GetPlaybook(id)
}

// CreatePlaybook creates a new playbook from markdown.
func (a *App) CreatePlaybook(req brainbox.CreatePlaybookRequest) (brainbox.Playbook, error) {
	return a.client.CreatePlaybook(req)
}

// UpdatePlaybook updates a playbook's name and/or markdown instructions.
func (a *App) UpdatePlaybook(id string, req brainbox.UpdatePlaybookRequest) (brainbox.Playbook, error) {
	return a.client.UpdatePlaybook(id, req)
}

// DeletePlaybook deletes a playbook (cancels it first if running).
func (a *App) DeletePlaybook(id string) error {
	return a.client.DeletePlaybook(id)
}

// RunPlaybook starts sequential execution of a playbook.
// workspaceProfile and runner override the playbook's saved values for this run.
func (a *App) RunPlaybook(id, workspaceProfile, runner string) (brainbox.Playbook, error) {
	return a.client.RunPlaybook(id, workspaceProfile, runner)
}

// CancelPlaybook cancels a running playbook.
func (a *App) CancelPlaybook(id string) error {
	return a.client.CancelPlaybook(id)
}

