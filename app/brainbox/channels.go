package brainbox

import "fmt"

// ChannelParticipant represents a participant in a group channel.
type ChannelParticipant struct {
	Name        string `json:"name"`
	Type        string `json:"type"` // "session", "ollama", or "user"
	SessionName string `json:"session_name,omitempty"`
	OllamaModel string `json:"ollama_model,omitempty"`
	SystemPrompt string `json:"system_prompt,omitempty"`
	JoinedAt    int64  `json:"joined_at"`
}

// ChannelMessage represents a message in a group channel.
type ChannelMessage struct {
	ID              string `json:"id"`
	ChannelID       string `json:"channel_id"`
	FromParticipant string `json:"from_participant"`
	Content         string `json:"content"`
	Summary         string `json:"summary,omitempty"`
	AddressedTo     string `json:"addressed_to,omitempty"`
	Type            string `json:"type"` // "message", "join", "completion"
	Timestamp       int64  `json:"timestamp"`
}

// Channel represents a group chat channel.
type Channel struct {
	ID           string               `json:"id"`
	Name         string               `json:"name"`
	Participants []ChannelParticipant `json:"participants"`
	Status       string               `json:"status"` // "active" or "completed"
	CreatedAt    int64                `json:"created_at"`
	CompletedAt  *int64               `json:"completed_at,omitempty"`
	CompletedBy  string               `json:"completed_by,omitempty"`
}

// CreateChannelRequest is the payload for POST /api/hub/channels.
type CreateChannelRequest struct {
	Name         string                       `json:"name"`
	Participants []ChannelParticipantRequest  `json:"participants"`
}

// ChannelParticipantRequest is a participant spec in CreateChannelRequest.
type ChannelParticipantRequest struct {
	Name         string `json:"name"`
	Type         string `json:"type"`
	SessionName  string `json:"session_name,omitempty"`
	OllamaModel  string `json:"ollama_model,omitempty"`
	SystemPrompt string `json:"system_prompt,omitempty"`
}

// PostChannelMessageRequest is the payload for POST /api/hub/channels/{id}/messages.
type PostChannelMessageRequest struct {
	FromParticipant string `json:"from_participant"`
	Content         string `json:"content"`
	Summary         string `json:"summary,omitempty"`
	AddressedTo     string `json:"addressed_to,omitempty"`
}

// CompleteChannelRequest is the payload for POST /api/hub/channels/{id}/complete.
type CompleteChannelRequest struct {
	By     string `json:"by"`
	Reason string `json:"reason,omitempty"`
}

// ListChannels returns all group chat channels.
func (c *Client) ListChannels() ([]Channel, error) {
	var channels []Channel
	if err := c.get("/api/hub/channels", &channels); err != nil {
		return nil, err
	}
	return channels, nil
}

// GetChannel returns a single channel by ID.
func (c *Client) GetChannel(id string) (Channel, error) {
	var channel Channel
	if err := c.get(fmt.Sprintf("/api/hub/channels/%s", id), &channel); err != nil {
		return channel, err
	}
	return channel, nil
}

// CreateChannel creates a new group chat channel and bootstraps session participants.
func (c *Client) CreateChannel(req CreateChannelRequest) (Channel, error) {
	var channel Channel
	if err := c.post("/api/hub/channels", req, &channel); err != nil {
		return channel, err
	}
	return channel, nil
}

// GetChannelMessages returns messages for a channel, optionally since a given message ID.
func (c *Client) GetChannelMessages(id, sinceID string) ([]ChannelMessage, error) {
	path := fmt.Sprintf("/api/hub/channels/%s/messages", id)
	if sinceID != "" {
		path += "?since_id=" + sinceID
	}
	var messages []ChannelMessage
	if err := c.get(path, &messages); err != nil {
		return nil, err
	}
	return messages, nil
}

// PostChannelMessage posts a message to a channel.
func (c *Client) PostChannelMessage(id string, req PostChannelMessageRequest) (ChannelMessage, error) {
	var msg ChannelMessage
	if err := c.post(fmt.Sprintf("/api/hub/channels/%s/messages", id), req, &msg); err != nil {
		return msg, err
	}
	return msg, nil
}

// CompleteChannel signals that a channel discussion is complete.
func (c *Client) CompleteChannel(id string, req CompleteChannelRequest) (Channel, error) {
	var channel Channel
	if err := c.post(fmt.Sprintf("/api/hub/channels/%s/complete", id), req, &channel); err != nil {
		return channel, err
	}
	return channel, nil
}

// DeleteChannel deletes a channel and all its messages.
func (c *Client) DeleteChannel(id string) error {
	return c.delete(fmt.Sprintf("/api/hub/channels/%s", id), nil)
}

// AddChannelParticipant attaches a session (or other participant) to an
// already-created channel. Lets the UI drop agents into a live conversation
// instead of fixing the roster at creation time.
func (c *Client) AddChannelParticipant(id string, req ChannelParticipantRequest) (Channel, error) {
	var channel Channel
	if err := c.post(fmt.Sprintf("/api/hub/channels/%s/participants", id), req, &channel); err != nil {
		return channel, err
	}
	return channel, nil
}

// RemoveChannelParticipant detaches a participant by name. Historical messages
// they posted remain in the channel log.
func (c *Client) RemoveChannelParticipant(id, name string) (Channel, error) {
	var channel Channel
	if err := c.delete(fmt.Sprintf("/api/hub/channels/%s/participants/%s", id, name), &channel); err != nil {
		return channel, err
	}
	return channel, nil
}

// JoinChannel registers the calling session as a participant using its bearer
// token identity. Idempotent — safe to call if already a member.
// This is a session-initiated join (as opposed to AddChannelParticipant which
// is app-initiated). Only used by agents running inside containers.
func (c *Client) JoinChannel(id string) (Channel, error) {
	var channel Channel
	if err := c.post(fmt.Sprintf("/api/hub/channels/%s/join", id), nil, &channel); err != nil {
		return channel, err
	}
	return channel, nil
}
