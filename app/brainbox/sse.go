package brainbox

import (
	"bufio"
	"context"
	"fmt"
	"log"
	"math"
	"net/http"
	"strings"
	"sync"
	"time"
)

// SSEListener connects to /api/events and forwards events via a callback.
type SSEListener struct {
	client     *Client
	onEvent    func(string)
	cancel     context.CancelFunc
	stopped    chan struct{} // closed when loop() exits; nil when not running
	mu         sync.Mutex
	running    bool
	httpClient *http.Client // no Timeout: context cancellation handles shutdown
}

// NewSSEListener creates an SSE listener that calls onEvent for each incoming event.
func NewSSEListener(client *Client, onEvent func(string)) *SSEListener {
	return &SSEListener{
		client:     client,
		onEvent:    onEvent,
		httpClient: &http.Client{}, // zero Timeout — relies on request context
	}
}

// Start begins listening in a goroutine. Reconnects with exponential backoff.
func (s *SSEListener) Start() {
	s.mu.Lock()
	if s.running {
		s.mu.Unlock()
		return
	}
	s.running = true
	stopped := make(chan struct{})
	s.stopped = stopped
	ctx, cancel := context.WithCancel(context.Background())
	s.cancel = cancel
	s.mu.Unlock()

	go func() {
		defer close(stopped)
		s.loop(ctx)
	}()
}

// Stop halts the SSE listener.
func (s *SSEListener) Stop() {
	s.mu.Lock()
	if s.cancel != nil {
		s.cancel()
	}
	s.running = false
	stopped := s.stopped
	s.mu.Unlock()

	if stopped != nil {
		select {
		case <-stopped:
		case <-time.After(2 * time.Second):
		}
	}
}

// Restart stops and restarts the listener (use when URL/key changes).
func (s *SSEListener) Restart() {
	s.Stop()
	s.Start()
}

func (s *SSEListener) loop(ctx context.Context) {
	attempt := 0
	maxDelay := 30 * time.Second

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		if err := s.connect(ctx); err != nil {
			select {
			case <-ctx.Done():
				return
			default:
			}
			delay := time.Duration(math.Min(
				float64(time.Second)*math.Pow(2, float64(attempt)),
				float64(maxDelay),
			))
			attempt++
			log.Printf("SSE disconnected (attempt %d), reconnecting in %s: %v", attempt, delay, err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(delay):
			}
		} else {
			attempt = 0
		}
	}
}

func (s *SSEListener) connect(ctx context.Context) error {
	url := s.client.BaseURL() + "/api/events"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Cache-Control", "no-cache")
	if key := s.client.APIKey(); key != "" {
		req.Header.Set("X-API-Key", key)
	}

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("connect: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}

	scanner := bufio.NewScanner(resp.Body)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "data: ") {
			data := strings.TrimPrefix(line, "data: ")
			s.onEvent(data)
		}
		select {
		case <-ctx.Done():
			return nil
		default:
		}
	}

	if err := scanner.Err(); err != nil {
		return fmt.Errorf("scan: %w", err)
	}
	return fmt.Errorf("stream closed")
}
