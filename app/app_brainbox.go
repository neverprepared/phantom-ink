package main

import "phantom-ink/brainbox"

// ---------------------------------------------------------------------------
// Sessions — pass-through to brainbox client
// ---------------------------------------------------------------------------

func (a *App) GetSessions() ([]brainbox.Session, error) {
	return a.client.ListSessions()
}

func (a *App) CreateSession(req brainbox.CreateSessionRequest) (brainbox.SessionActionResponse, error) {
	return a.client.CreateSession(req)
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

func (a *App) ListTasks(status string) ([]brainbox.Task, error) {
	return a.client.ListTasks(status)
}

func (a *App) SubmitTask(req brainbox.SubmitTaskRequest) (brainbox.Task, error) {
	return a.client.SubmitTask(req)
}

func (a *App) CancelTask(taskID string) error {
	return a.client.CancelTask(taskID)
}

func (a *App) ListAgents() ([]brainbox.Agent, error) {
	return a.client.ListAgents()
}

func (a *App) GetMessageLog() ([]brainbox.Message, error) {
	return a.client.GetMessageLog()
}

// ---------------------------------------------------------------------------
// Repos
// ---------------------------------------------------------------------------

func (a *App) ListRepos() ([]brainbox.Repo, error) {
	return a.client.ListRepos()
}

func (a *App) AddRepo(req brainbox.AddRepoRequest) (brainbox.Repo, error) {
	return a.client.AddRepo(req)
}

func (a *App) UpdateRepo(name string, req brainbox.UpdateRepoRequest) (brainbox.Repo, error) {
	return a.client.UpdateRepo(name, req)
}

func (a *App) DeleteRepo(name string) error {
	return a.client.DeleteRepo(name)
}

// ---------------------------------------------------------------------------
// Pipelines
// ---------------------------------------------------------------------------

func (a *App) ListPipelines() ([]brainbox.Pipeline, error) {
	return a.client.ListPipelines()
}

func (a *App) ListPipelineRuns() ([]brainbox.PipelineRun, error) {
	return a.client.ListPipelineRuns()
}

func (a *App) StartPipelineRun(name string, params map[string]interface{}) (brainbox.PipelineRun, error) {
	return a.client.StartPipelineRun(name, params)
}

func (a *App) CancelPipelineRun(runID string) error {
	return a.client.CancelPipelineRun(runID)
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

func (a *App) ListArtifacts(prefix string) ([]brainbox.Artifact, error) {
	return a.client.ListArtifacts(prefix)
}

func (a *App) DownloadArtifact(key string) ([]byte, error) {
	return a.client.DownloadArtifact(key)
}

func (a *App) DeleteArtifact(key string) error {
	return a.client.DeleteArtifact(key)
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

func (a *App) GetSessionTraces(sessionName string, limit int) ([]brainbox.Trace, error) {
	return a.client.GetSessionTraces(sessionName, limit)
}

func (a *App) GetTraceDetail(traceID string) (brainbox.TraceDetail, error) {
	return a.client.GetTraceDetail(traceID)
}
