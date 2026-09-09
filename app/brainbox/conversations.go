package brainbox

import (
	"fmt"
	"net/url"
)

// Types and client methods for the multi-agent Chat engine (brainbox
// /api/conversations). This is the successor to the channels API in
// channels.go; both exist during the phased migration and share no state.
//
// Every call carries an explicit profile — the server scopes each read and
// write to it, so an omitted or wrong profile is a 4xx, not a wildcard.

// ConversationParticipant is a member of a conversation. A "persona" is a
// lightweight LLM participant driven server-side through the complete() seam;
// "human" posts through the API; "session" is the promotion path (not yet
// driven).
type ConversationParticipant struct {
	Name        string         `json:"name"`
	Kind        string         `json:"kind"` // "human" | "persona" | "session"
	ModelTarget map[string]any `json:"model_target,omitempty"`
	RolePrompt  string         `json:"role_prompt,omitempty"`
	CooldownS   float64        `json:"cooldown_s,omitempty"`
	JoinedAt    int64          `json:"joined_at"`
}

// Conversation is a chat room record in the local-first store.
type Conversation struct {
	ID           string                    `json:"id"` // ULID
	Profile      string                    `json:"profile"`
	Title        string                    `json:"title"`
	Status       string                    `json:"status"` // "active" | "archived"
	Participants []ConversationParticipant `json:"participants"`
	CreatedAt    int64                     `json:"created_at"`
	UpdatedAt    int64                     `json:"updated_at"`
	NodeID       string                    `json:"node_id,omitempty"`
}

// ConversationMessage is one entry in a conversation's append-only log.
type ConversationMessage struct {
	ID             string `json:"id"` // ULID — also the sort key
	ConversationID string `json:"conversation_id"`
	Profile        string `json:"profile"`
	Author         string `json:"author"`
	Kind           string `json:"kind"` // "message" | "system" | "join" | "tool" | "session"
	Content        string `json:"content"`
	AddressedTo    string `json:"addressed_to,omitempty"`
	InReplyTo      string `json:"in_reply_to,omitempty"`
	CreatedAt      int64  `json:"created_at"`
	NodeID         string `json:"node_id,omitempty"`
}

// CreateConversationRequest is the payload for POST /api/conversations.
type CreateConversationRequest struct {
	Title        string                           `json:"title"`
	Profile      string                           `json:"profile"`
	Participants []ConversationParticipantRequest `json:"participants"`
}

// ConversationParticipantRequest is a participant spec at creation time.
type ConversationParticipantRequest struct {
	Name        string         `json:"name"`
	Kind        string         `json:"kind"`
	ModelTarget map[string]any `json:"model_target,omitempty"`
	RolePrompt  string         `json:"role_prompt,omitempty"`
	CooldownS   float64        `json:"cooldown_s,omitempty"`
}

// PostConversationMessageRequest is the payload for posting a human message.
type PostConversationMessageRequest struct {
	Author      string `json:"author"`
	Content     string `json:"content"`
	AddressedTo string `json:"addressed_to,omitempty"`
}

// conversationPath builds a conversation URL with the profile (and any extra
// query values) attached. Centralized so no call site can forget the profile.
func conversationPath(suffix, profile string, extra url.Values) string {
	q := url.Values{}
	for k, v := range extra {
		q[k] = v
	}
	q.Set("profile", profile)
	return "/api/conversations" + suffix + "?" + q.Encode()
}

// ListConversations returns the profile's conversations, newest activity first.
func (c *Client) ListConversations(profile string, includeArchived bool) ([]Conversation, error) {
	extra := url.Values{}
	extra.Set("include_archived", fmt.Sprintf("%t", includeArchived))
	var out []Conversation
	if err := c.get(conversationPath("", profile, extra), &out); err != nil {
		return nil, err
	}
	return out, nil
}

// GetConversation returns one conversation. A conversation in another profile
// reads as "not found".
func (c *Client) GetConversation(id, profile string) (Conversation, error) {
	var out Conversation
	err := c.get(conversationPath("/"+url.PathEscape(id), profile, nil), &out)
	return out, err
}

// CreateConversation creates a room. The profile travels in the body here
// (it is part of the record being created), not the query string.
func (c *Client) CreateConversation(req CreateConversationRequest) (Conversation, error) {
	var out Conversation
	err := c.post("/api/conversations", req, &out)
	return out, err
}

// ArchiveConversation flips a room to archived; its messages stay readable.
func (c *Client) ArchiveConversation(id, profile string) (Conversation, error) {
	var out Conversation
	err := c.post(conversationPath("/"+url.PathEscape(id)+"/archive", profile, nil), nil, &out)
	return out, err
}

// ListConversationMessages returns messages in ULID order. A non-empty sinceID
// returns only messages created after it.
func (c *Client) ListConversationMessages(id, profile, sinceID string) ([]ConversationMessage, error) {
	extra := url.Values{}
	if sinceID != "" {
		extra.Set("since_id", sinceID)
	}
	var out []ConversationMessage
	path := conversationPath("/"+url.PathEscape(id)+"/messages", profile, extra)
	if err := c.get(path, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// PostConversationMessage posts a human message. It returns as soon as the
// message is durable; any persona reply arrives on the conversation's SSE
// stream, not in this response.
func (c *Client) PostConversationMessage(id, profile string, req PostConversationMessageRequest) (ConversationMessage, error) {
	var out ConversationMessage
	err := c.post(conversationPath("/"+url.PathEscape(id)+"/messages", profile, nil), req, &out)
	return out, err
}

// ConversationStreamURL is the per-conversation SSE endpoint.
func (c *Client) ConversationStreamURL(id, profile string) string {
	return c.BaseURL() + conversationPath("/"+url.PathEscape(id)+"/stream", profile, nil)
}
