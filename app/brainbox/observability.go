package brainbox

// HealthStatus is a generic health check response.
type HealthStatus struct {
	Status  string `json:"status"`
	Message string `json:"message"`
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

// GetQdrantHealth checks the Qdrant service health.
func (c *Client) GetQdrantHealth() (HealthStatus, error) {
	var h HealthStatus
	if err := c.get("/api/qdrant/health", &h); err != nil {
		return h, err
	}
	return h, nil
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
