package brainbox

import (
	"fmt"
	"net/url"
)

// HealthStatus is a generic health check response.
type HealthStatus struct {
	Status  string `json:"status"`
	Message string `json:"message"`
}

// Trace represents a LangFuse trace.
type Trace struct {
	ID        string      `json:"id"`
	Name      string      `json:"name"`
	Timestamp string      `json:"timestamp"`
	Model     string      `json:"model"`
	Usage     interface{} `json:"usage"`
	Metadata  interface{} `json:"metadata"`
}

// TraceDetail is the full detail of a single trace.
type TraceDetail struct {
	ID         string      `json:"id"`
	Name       string      `json:"name"`
	Timestamp  string      `json:"timestamp"`
	Spans      interface{} `json:"spans"`
	Metadata   interface{} `json:"metadata"`
	TokenUsage interface{} `json:"token_usage"`
}

// ContainerMetrics represents CPU/memory/uptime for a container.
type ContainerMetrics struct {
	Name   string  `json:"name"`
	CPU    float64 `json:"cpu_percent"`
	Memory int64   `json:"memory_bytes"`
	Uptime string  `json:"uptime"`
}

// MetricsSample is one data point in the aggregate metrics history ring buffer.
type MetricsSample struct {
	Timestamp  float64 `json:"ts"`
	AgentCount int     `json:"agent_count"`
	TotalCPU   float64 `json:"total_cpu"`
	TotalMem   int64   `json:"total_mem"`
}

// SessionMetricsSample is one per-session data point in the metrics history.
type SessionMetricsSample struct {
	Timestamp  float64 `json:"ts"`
	MemUsage   int64   `json:"mem_usage"`
	CPUPercent float64 `json:"cpu_percent"`
}

// GetLangfuseHealth checks the LangFuse service health.
func (c *Client) GetLangfuseHealth() (HealthStatus, error) {
	var h HealthStatus
	if err := c.get("/api/langfuse/health", &h); err != nil {
		return h, err
	}
	return h, nil
}

// GetQdrantHealth checks the Qdrant service health.
func (c *Client) GetQdrantHealth() (HealthStatus, error) {
	var h HealthStatus
	if err := c.get("/api/qdrant/health", &h); err != nil {
		return h, err
	}
	return h, nil
}

// GetSessionTraces returns LangFuse traces for a session.
func (c *Client) GetSessionTraces(sessionName string, limit int) ([]Trace, error) {
	var traces []Trace
	path := fmt.Sprintf("/api/langfuse/sessions/%s/traces?limit=%d",
		url.PathEscape(sessionName), limit)
	if err := c.get(path, &traces); err != nil {
		return nil, err
	}
	return traces, nil
}

// GetSessionSummary returns a trace count summary for a session.
func (c *Client) GetSessionSummary(sessionName string) (map[string]interface{}, error) {
	var summary map[string]interface{}
	path := fmt.Sprintf("/api/langfuse/sessions/%s/summary", url.PathEscape(sessionName))
	if err := c.get(path, &summary); err != nil {
		return nil, err
	}
	return summary, nil
}

// GetTraceDetail returns the full detail of a single trace.
func (c *Client) GetTraceDetail(traceID string) (TraceDetail, error) {
	var detail TraceDetail
	path := fmt.Sprintf("/api/langfuse/traces/%s", url.PathEscape(traceID))
	if err := c.get(path, &detail); err != nil {
		return detail, err
	}
	return detail, nil
}

// GetContainerMetrics returns CPU/memory metrics for all containers.
func (c *Client) GetContainerMetrics() ([]ContainerMetrics, error) {
	var metrics []ContainerMetrics
	if err := c.get("/api/metrics/containers", &metrics); err != nil {
		return nil, err
	}
	return metrics, nil
}

// GetMetricsHistory returns the aggregate metrics ring buffer (last hour at 10 s resolution).
func (c *Client) GetMetricsHistory() ([]MetricsSample, error) {
	var samples []MetricsSample
	if err := c.get("/api/metrics/history", &samples); err != nil {
		return nil, err
	}
	return samples, nil
}

// GetSessionsMetricsHistory returns per-session metrics ring buffers keyed by session name.
func (c *Client) GetSessionsMetricsHistory() (map[string][]SessionMetricsSample, error) {
	var result map[string][]SessionMetricsSample
	if err := c.get("/api/metrics/sessions/history", &result); err != nil {
		return nil, err
	}
	return result, nil
}
