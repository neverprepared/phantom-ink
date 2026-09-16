package brainbox

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync"
	"testing"
	"time"
)

// --------------------------------------------------------------------------
// Client
// --------------------------------------------------------------------------

func TestListConversationsSendsProfile(t *testing.T) {
	var gotQuery url.Values
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.Query()
		if r.URL.Path != "/api/conversations" {
			t.Errorf("path = %q", r.URL.Path)
		}
		_ = json.NewEncoder(w).Encode([]Conversation{{ID: "01A", Title: "room", Profile: "personal"}})
	}))
	defer srv.Close()

	convs, err := NewClient(srv.URL, "").ListConversations("personal", false)
	if err != nil {
		t.Fatalf("ListConversations: %v", err)
	}
	if len(convs) != 1 || convs[0].Title != "room" {
		t.Fatalf("unexpected result: %+v", convs)
	}
	if gotQuery.Get("profile") != "personal" {
		t.Errorf("profile = %q, want personal", gotQuery.Get("profile"))
	}
	if gotQuery.Get("include_archived") != "false" {
		t.Errorf("include_archived = %q, want false", gotQuery.Get("include_archived"))
	}
}

func TestGetConversationScopesByProfileAndEscapesID(t *testing.T) {
	var gotPath, gotProfile string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.EscapedPath()
		gotProfile = r.URL.Query().Get("profile")
		_ = json.NewEncoder(w).Encode(Conversation{ID: "a/b"})
	}))
	defer srv.Close()

	if _, err := NewClient(srv.URL, "").GetConversation("a/b", "work"); err != nil {
		t.Fatalf("GetConversation: %v", err)
	}
	if gotPath != "/api/conversations/a%2Fb" {
		t.Errorf("path = %q, want the id percent-escaped", gotPath)
	}
	if gotProfile != "work" {
		t.Errorf("profile = %q", gotProfile)
	}
}

func TestCreateConversationPostsRoster(t *testing.T) {
	var got CreateConversationRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("method = %s", r.Method)
		}
		_ = json.NewDecoder(r.Body).Decode(&got)
		_ = json.NewEncoder(w).Encode(Conversation{ID: "01B", Title: got.Title})
	}))
	defer srv.Close()

	conv, err := NewClient(srv.URL, "").CreateConversation(CreateConversationRequest{
		Title:   "planning",
		Profile: "personal",
		Participants: []ConversationParticipantRequest{{
			Name:        "sage",
			Kind:        "persona",
			ModelTarget: map[string]any{"provider": "ollama", "model": "qwen3:8b"},
			RolePrompt:  "Be terse.",
		}},
	})
	if err != nil {
		t.Fatalf("CreateConversation: %v", err)
	}
	if conv.Title != "planning" {
		t.Errorf("title = %q", conv.Title)
	}
	if got.Participants[0].ModelTarget["model"] != "qwen3:8b" {
		t.Errorf("model_target not round-tripped: %+v", got.Participants[0])
	}
}

func TestListConversationMessagesPassesSinceID(t *testing.T) {
	var gotSince string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotSince = r.URL.Query().Get("since_id")
		_ = json.NewEncoder(w).Encode([]ConversationMessage{{ID: "01C", Content: "hi"}})
	}))
	defer srv.Close()

	msgs, err := NewClient(srv.URL, "").ListConversationMessages("01B", "personal", "01A")
	if err != nil {
		t.Fatalf("ListConversationMessages: %v", err)
	}
	if len(msgs) != 1 || msgs[0].Content != "hi" {
		t.Fatalf("unexpected messages: %+v", msgs)
	}
	if gotSince != "01A" {
		t.Errorf("since_id = %q", gotSince)
	}
}

func TestPostConversationMessageAndArchive(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/conversations/01B/messages":
			var req PostConversationMessageRequest
			_ = json.NewDecoder(r.Body).Decode(&req)
			_ = json.NewEncoder(w).Encode(ConversationMessage{ID: "01C", Author: req.Author, Content: req.Content})
		case r.URL.Path == "/api/conversations/01B/archive":
			_ = json.NewEncoder(w).Encode(Conversation{ID: "01B", Status: "archived"})
		default:
			t.Errorf("unexpected path %q", r.URL.Path)
		}
	}))
	defer srv.Close()

	c := NewClient(srv.URL, "")
	msg, err := c.PostConversationMessage("01B", "personal", PostConversationMessageRequest{
		Author: "user", Content: "hello",
	})
	if err != nil {
		t.Fatalf("PostConversationMessage: %v", err)
	}
	if msg.Author != "user" || msg.Content != "hello" {
		t.Errorf("echoed message = %+v", msg)
	}

	conv, err := c.ArchiveConversation("01B", "personal")
	if err != nil {
		t.Fatalf("ArchiveConversation: %v", err)
	}
	if conv.Status != "archived" {
		t.Errorf("status = %q", conv.Status)
	}
}

func TestConversationErrorsSurfaceStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"Conversation '01B' not found"}`))
	}))
	defer srv.Close()

	// A conversation owned by another profile reads as a 404 — the client must
	// surface it rather than returning a zero-valued room.
	if _, err := NewClient(srv.URL, "").GetConversation("01B", "work"); err == nil {
		t.Fatal("expected an error for a 404 response")
	}
}

// --------------------------------------------------------------------------
// Per-conversation SSE bridge
// --------------------------------------------------------------------------

// sseServer streams the given frames, then holds the connection open until the
// client disconnects (matching a real SSE endpoint's behaviour).
func sseServer(t *testing.T, frames []string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flusher, ok := w.(http.Flusher)
		if !ok {
			t.Error("ResponseWriter is not a Flusher")
			return
		}
		for _, f := range frames {
			fmt.Fprintf(w, "data: %s\n\n", f)
			flusher.Flush()
		}
		<-r.Context().Done()
	}))
}

type collector struct {
	mu     sync.Mutex
	events []string
	ids    []string
	got    chan struct{}
}

func newCollector() *collector { return &collector{got: make(chan struct{}, 64)} }

func (c *collector) onEvent(conversationID, data string) {
	c.mu.Lock()
	c.events = append(c.events, data)
	c.ids = append(c.ids, conversationID)
	c.mu.Unlock()
	select {
	case c.got <- struct{}{}:
	default:
	}
}

func (c *collector) waitFor(t *testing.T, n int) []string {
	t.Helper()
	deadline := time.After(5 * time.Second)
	for {
		c.mu.Lock()
		if len(c.events) >= n {
			out := append([]string(nil), c.events...)
			c.mu.Unlock()
			return out
		}
		c.mu.Unlock()
		select {
		case <-c.got:
		case <-deadline:
			t.Fatalf("timed out waiting for %d events", n)
		}
	}
}

func TestConversationStreamDeliversFramesInOrder(t *testing.T) {
	// The PR1 SSE contract for one persona reply.
	frames := []string{
		`{"event":"connected","conversation_id":"01B"}`,
		`{"event":"thinking","conversation_id":"01B","data":{"author":"sage"}}`,
		`{"event":"message.created","conversation_id":"01B","data":{"message":{"id":"01C","content":""}}}`,
		`{"event":"message.delta","conversation_id":"01B","data":{"id":"01C","delta":"Hel"}}`,
		`{"event":"message.delta","conversation_id":"01B","data":{"id":"01C","delta":"lo"}}`,
		`{"event":"message.done","conversation_id":"01B","data":{"message":{"id":"01C","content":"Hello"}}}`,
	}
	srv := sseServer(t, frames)
	defer srv.Close()

	col := newCollector()
	streams := NewConversationStreams(NewClient(srv.URL, "test-key"), col.onEvent)
	streams.Subscribe("01B", "personal")
	defer streams.UnsubscribeAll()

	got := col.waitFor(t, len(frames))
	for i, want := range frames {
		if got[i] != want {
			t.Fatalf("frame %d = %s, want %s", i, got[i], want)
		}
	}

	col.mu.Lock()
	defer col.mu.Unlock()
	for _, id := range col.ids {
		if id != "01B" {
			t.Fatalf("frame tagged with conversation %q", id)
		}
	}
}

func TestConversationStreamSendsProfileAndAPIKey(t *testing.T) {
	reqs := make(chan *http.Request, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case reqs <- r:
		default:
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer srv.Close()

	streams := NewConversationStreams(NewClient(srv.URL, "secret-key"), func(string, string) {})
	streams.Subscribe("01B", "work")
	defer streams.UnsubscribeAll()

	select {
	case r := <-reqs:
		if got := r.URL.Query().Get("profile"); got != "work" {
			t.Errorf("profile = %q", got)
		}
		if got := r.Header.Get("X-API-Key"); got != "secret-key" {
			t.Errorf("X-API-Key = %q", got)
		}
		if got := r.Header.Get("Accept"); got != "text/event-stream" {
			t.Errorf("Accept = %q", got)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("no request reached the server")
	}
}

func TestConversationStreamUnsubscribeStopsDelivery(t *testing.T) {
	srv := sseServer(t, []string{`{"event":"connected"}`})
	defer srv.Close()

	col := newCollector()
	streams := NewConversationStreams(NewClient(srv.URL, ""), col.onEvent)
	streams.Subscribe("01B", "personal")
	col.waitFor(t, 1)

	if !streams.Active("01B") {
		t.Fatal("stream should be active after Subscribe")
	}
	streams.Unsubscribe("01B")
	if streams.Active("01B") {
		t.Fatal("stream should be inactive after Unsubscribe")
	}
	// Unsubscribing twice is a no-op, not a panic or a hang.
	streams.Unsubscribe("01B")
}

func TestConversationStreamResubscribeReplacesSubscription(t *testing.T) {
	srv := sseServer(t, []string{`{"event":"connected"}`})
	defer srv.Close()

	col := newCollector()
	streams := NewConversationStreams(NewClient(srv.URL, ""), col.onEvent)
	streams.Subscribe("01B", "personal")
	col.waitFor(t, 1)
	streams.Subscribe("01B", "personal") // replaces, does not double up
	col.waitFor(t, 2)
	defer streams.UnsubscribeAll()

	if !streams.Active("01B") {
		t.Fatal("stream should still be active")
	}
}

func TestConversationStreamsAreIndependentPerRoom(t *testing.T) {
	srv := sseServer(t, []string{`{"event":"connected"}`})
	defer srv.Close()

	streams := NewConversationStreams(NewClient(srv.URL, ""), func(string, string) {})
	streams.Subscribe("01A", "personal")
	streams.Subscribe("01B", "personal")
	defer streams.UnsubscribeAll()

	streams.Unsubscribe("01A")
	if streams.Active("01A") {
		t.Error("01A should be closed")
	}
	if !streams.Active("01B") {
		t.Error("closing one room must not close another")
	}
}

// --------------------------------------------------------------------------
// Participant management (PR2 — the persona-management UI's write path)
// --------------------------------------------------------------------------

func TestAddConversationParticipantPostsSpec(t *testing.T) {
	var gotPath, gotProfile string
	var gotBody AddConversationParticipantRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.EscapedPath()
		gotProfile = r.URL.Query().Get("profile")
		if r.Method != http.MethodPost {
			t.Errorf("method = %q, want POST", r.Method)
		}
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_ = json.NewEncoder(w).Encode(Conversation{
			ID:           "01A",
			Participants: []ConversationParticipant{{Name: "sage", Kind: "persona"}},
		})
	}))
	defer srv.Close()

	cooldown := 12.5
	conv, err := NewClient(srv.URL, "").AddConversationParticipant("01A", "personal",
		AddConversationParticipantRequest{
			Name:        "sage",
			Kind:        "persona",
			ModelTarget: map[string]any{"provider": "ollama", "model": "qwen3:8b"},
			RolePrompt:  "Be terse.",
			CooldownS:   &cooldown,
		})
	if err != nil {
		t.Fatalf("AddConversationParticipant: %v", err)
	}
	if gotPath != "/api/conversations/01A/participants" {
		t.Errorf("path = %q", gotPath)
	}
	if gotProfile != "personal" {
		t.Errorf("profile = %q", gotProfile)
	}
	if gotBody.Name != "sage" || gotBody.RolePrompt != "Be terse." {
		t.Errorf("body = %+v", gotBody)
	}
	// A pointer cooldown is what lets 0 ("never cool down") travel; a plain
	// float64 with omitempty would drop it and silently mean "use the default".
	if gotBody.CooldownS == nil || *gotBody.CooldownS != 12.5 {
		t.Errorf("cooldown_s = %v, want 12.5", gotBody.CooldownS)
	}
	if len(conv.Participants) != 1 {
		t.Errorf("roster = %+v", conv.Participants)
	}
}

func TestAddConversationParticipantSendsZeroCooldown(t *testing.T) {
	var raw map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&raw)
		_ = json.NewEncoder(w).Encode(Conversation{ID: "01A"})
	}))
	defer srv.Close()

	zero := 0.0
	_, err := NewClient(srv.URL, "").AddConversationParticipant("01A", "personal",
		AddConversationParticipantRequest{Name: "sage", Kind: "persona", CooldownS: &zero})
	if err != nil {
		t.Fatalf("AddConversationParticipant: %v", err)
	}
	if v, ok := raw["cooldown_s"]; !ok || v.(float64) != 0 {
		t.Errorf("cooldown_s = %v (present=%v), want an explicit 0", v, ok)
	}
}

func TestRemoveConversationParticipantEscapesName(t *testing.T) {
	var gotPath, gotMethod string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.EscapedPath()
		gotMethod = r.Method
		_ = json.NewEncoder(w).Encode(Conversation{ID: "01A"})
	}))
	defer srv.Close()

	if _, err := NewClient(srv.URL, "").RemoveConversationParticipant("01A", "personal", "a/b"); err != nil {
		t.Fatalf("RemoveConversationParticipant: %v", err)
	}
	if gotMethod != http.MethodDelete {
		t.Errorf("method = %q, want DELETE", gotMethod)
	}
	if gotPath != "/api/conversations/01A/participants/a%2Fb" {
		t.Errorf("path = %q, want the name percent-escaped", gotPath)
	}
}

func TestParticipantErrorsSurfaceStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"Participant 'ghost' not found"}`))
	}))
	defer srv.Close()

	if _, err := NewClient(srv.URL, "").RemoveConversationParticipant("01A", "personal", "ghost"); err == nil {
		t.Fatal("expected an error for a 404")
	}
}
