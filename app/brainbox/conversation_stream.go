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

// ConversationStreams bridges per-conversation SSE streams into the app.
//
// The existing SSEListener (sse.go) bridges exactly one stream — the global
// /api/events firehose. A conversation's tokens arrive on their own endpoint,
// so the panel needs a second, per-room bridge that can be opened when a
// conversation is selected and closed when it is not. That is this type: a
// keyed set of listeners, at most one per conversation id, each with its own
// reconnect loop.
type ConversationStreams struct {
	client  *Client
	onEvent func(conversationID, data string)

	mu      sync.Mutex
	active  map[string]*conversationStream
	timeout time.Duration // per-connect dial timeout; 0 = none (streams are long-lived)
}

type conversationStream struct {
	cancel  context.CancelFunc
	stopped chan struct{}
}

// NewConversationStreams creates the bridge. onEvent is called for every SSE
// data line, tagged with the conversation it came from.
func NewConversationStreams(client *Client, onEvent func(conversationID, data string)) *ConversationStreams {
	return &ConversationStreams{
		client:  client,
		onEvent: onEvent,
		active:  make(map[string]*conversationStream),
	}
}

// Subscribe opens (or re-opens) the stream for one conversation. Subscribing to
// a conversation that is already streaming replaces the existing subscription,
// so a repeated call from the UI is safe and never doubles delivery.
func (s *ConversationStreams) Subscribe(conversationID, profile string) {
	s.Unsubscribe(conversationID)

	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})

	s.mu.Lock()
	s.active[conversationID] = &conversationStream{cancel: cancel, stopped: stopped}
	s.mu.Unlock()

	go func() {
		defer close(stopped)
		s.loop(ctx, conversationID, profile)
	}()
}

// Unsubscribe closes one conversation's stream and waits briefly for its
// goroutine to exit.
func (s *ConversationStreams) Unsubscribe(conversationID string) {
	s.mu.Lock()
	st, ok := s.active[conversationID]
	delete(s.active, conversationID)
	s.mu.Unlock()
	if !ok {
		return
	}
	st.cancel()
	select {
	case <-st.stopped:
	case <-time.After(2 * time.Second):
	}
}

// UnsubscribeAll closes every open conversation stream (app shutdown).
func (s *ConversationStreams) UnsubscribeAll() {
	s.mu.Lock()
	ids := make([]string, 0, len(s.active))
	for id := range s.active {
		ids = append(ids, id)
	}
	s.mu.Unlock()
	for _, id := range ids {
		s.Unsubscribe(id)
	}
}

// Active reports whether a conversation currently has an open stream.
func (s *ConversationStreams) Active(conversationID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	_, ok := s.active[conversationID]
	return ok
}

func (s *ConversationStreams) loop(ctx context.Context, conversationID, profile string) {
	attempt := 0
	maxDelay := 30 * time.Second

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		if err := s.connect(ctx, conversationID, profile); err != nil {
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
			log.Printf("conversation SSE %s disconnected (attempt %d), retrying in %s: %v",
				conversationID, attempt, delay, err)
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

func (s *ConversationStreams) connect(ctx context.Context, conversationID, profile string) error {
	url := s.client.ConversationStreamURL(conversationID, profile)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Cache-Control", "no-cache")
	if key := s.client.APIKey(); key != "" {
		req.Header.Set("X-API-Key", key)
	}

	// A zero Timeout is deliberate: an SSE connection is meant to stay open,
	// and cancellation comes from the context.
	resp, err := (&http.Client{Timeout: s.timeout}).Do(req)
	if err != nil {
		return fmt.Errorf("connect: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}

	scanner := bufio.NewScanner(resp.Body)
	// Token deltas are small, but a finalized message frame carries the whole
	// message — raise the line cap well above bufio's 64KB default.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		if line := scanner.Text(); strings.HasPrefix(line, "data: ") {
			s.onEvent(conversationID, strings.TrimPrefix(line, "data: "))
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
