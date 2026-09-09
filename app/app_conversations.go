package main

import (
	"encoding/json"
	"errors"
	"fmt"

	"phantom-ink/brainbox"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// Wails bindings for the multi-agent Chat engine.
//
// The CRUD methods are thin passthroughs to the brainbox client. The
// interesting part is Subscribe/Unsubscribe: until now the Go layer bridged
// only the global /api/events stream, so a conversation's token deltas had
// nowhere to land and the panel fell back to a 5-second poll. These two
// methods open and close a per-conversation SSE bridge whose frames are
// re-emitted to the frontend as the "conversation:event" Wails event.

// errNoClient is returned when a conversation method is called before the
// brainbox client exists (startup failure), mirroring errNoDB.
var errNoClient = errors.New("brainbox client not initialized")

func (a *App) requireClient() error {
	if a.client == nil {
		return errNoClient
	}
	return nil
}

// ListConversations returns a profile's conversations, newest activity first.
func (a *App) ListConversations(profile string, includeArchived bool) ([]brainbox.Conversation, error) {
	if err := a.requireClient(); err != nil {
		return nil, err
	}
	return a.client.ListConversations(profile, includeArchived)
}

// GetConversation returns one conversation within a profile.
func (a *App) GetConversation(id, profile string) (brainbox.Conversation, error) {
	if err := a.requireClient(); err != nil {
		return brainbox.Conversation{}, err
	}
	return a.client.GetConversation(id, profile)
}

// CreateConversation creates a room with its participant roster.
func (a *App) CreateConversation(req brainbox.CreateConversationRequest) (brainbox.Conversation, error) {
	if err := a.requireClient(); err != nil {
		return brainbox.Conversation{}, err
	}
	return a.client.CreateConversation(req)
}

// ArchiveConversation archives a room. Its history stays readable.
func (a *App) ArchiveConversation(id, profile string) (brainbox.Conversation, error) {
	if err := a.requireClient(); err != nil {
		return brainbox.Conversation{}, err
	}
	return a.client.ArchiveConversation(id, profile)
}

// ListConversationMessages returns messages in order; sinceID (a ULID) fetches
// only what came after it.
func (a *App) ListConversationMessages(id, profile, sinceID string) ([]brainbox.ConversationMessage, error) {
	if err := a.requireClient(); err != nil {
		return nil, err
	}
	return a.client.ListConversationMessages(id, profile, sinceID)
}

// PostConversationMessage posts a human message. The persona's reply arrives on
// the conversation stream, not in this return value.
func (a *App) PostConversationMessage(id, profile string, req brainbox.PostConversationMessageRequest) (brainbox.ConversationMessage, error) {
	if err := a.requireClient(); err != nil {
		return brainbox.ConversationMessage{}, err
	}
	return a.client.PostConversationMessage(id, profile, req)
}

// AddConversationParticipant adds or updates a persona on a live room (the
// persona-management UI's write path) and returns the updated roster.
func (a *App) AddConversationParticipant(id, profile string, req brainbox.AddConversationParticipantRequest) (brainbox.Conversation, error) {
	if err := a.requireClient(); err != nil {
		return brainbox.Conversation{}, err
	}
	return a.client.AddConversationParticipant(id, profile, req)
}

// RemoveConversationParticipant removes a participant by name.
func (a *App) RemoveConversationParticipant(id, profile, name string) (brainbox.Conversation, error) {
	if err := a.requireClient(); err != nil {
		return brainbox.Conversation{}, err
	}
	return a.client.RemoveConversationParticipant(id, profile, name)
}

// SubscribeConversation opens the per-conversation SSE bridge. Frames are
// re-emitted to the frontend as "conversation:event" with the parsed payload.
// Calling it again for the same conversation replaces the subscription.
func (a *App) SubscribeConversation(id, profile string) error {
	if err := a.requireClient(); err != nil {
		return err
	}
	if a.conversations == nil {
		return fmt.Errorf("conversation stream bridge not initialized")
	}
	a.conversations.Subscribe(id, profile)
	return nil
}

// UnsubscribeConversation closes the bridge for one conversation (the panel
// calls this when the room is deselected or the panel unmounts).
func (a *App) UnsubscribeConversation(id string) {
	if a.conversations != nil {
		a.conversations.Unsubscribe(id)
	}
}

// newConversationBridge builds the per-conversation SSE bridge, forwarding each
// frame to the frontend. Frames are JSON ({event, conversation_id, data}); a
// frame that fails to parse is forwarded as raw text rather than dropped, so a
// contract change shows up in the UI instead of vanishing.
func newConversationBridge(a *App) *brainbox.ConversationStreams {
	return brainbox.NewConversationStreams(a.client, func(conversationID, data string) {
		if a.ctx == nil {
			return
		}
		var frame map[string]any
		if err := json.Unmarshal([]byte(data), &frame); err != nil {
			runtime.EventsEmit(a.ctx, "conversation:event", map[string]any{
				"conversation_id": conversationID,
				"event":           "raw",
				"data":            data,
			})
			return
		}
		// Trust the stream's own id, but make sure one is always present.
		if _, ok := frame["conversation_id"]; !ok {
			frame["conversation_id"] = conversationID
		}
		runtime.EventsEmit(a.ctx, "conversation:event", frame)
	})
}
