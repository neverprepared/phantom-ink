package brainbox

// AuthorityInfo describes one runner that advertises secret_authority.
type AuthorityInfo struct {
	Name           string   `json:"name"`
	Version        string   `json:"version"`
	Tags           []string `json:"tags"`
	Online         bool     `json:"online"`
	LastSeen       int64    `json:"last_seen"`
	LastSeenAgeMs  int64    `json:"last_seen_age_ms"`
	LastSealAt     *int64   `json:"last_seal_at"`
	LastSealAgeMs  *int64   `json:"last_seal_age_ms"`
}

// SealFailure is one entry from the API's recent-failure ring buffer.
type SealFailure struct {
	When   int64  `json:"when"`
	Status int    `json:"status"`
	Error  string `json:"error"`
}

// AuthorityStatus is the response from GET /api/credentials/authority/status.
type AuthorityStatus struct {
	Authorities     []AuthorityInfo `json:"authorities"`
	AnyOnline       bool            `json:"any_online"`
	RecentFailures  []SealFailure   `json:"recent_failures"`
}

// GetAuthorityStatus polls the credential authority health endpoint.
func (c *Client) GetAuthorityStatus() (AuthorityStatus, error) {
	var s AuthorityStatus
	if err := c.do("GET", "/api/credentials/authority/status", nil, &s); err != nil {
		return AuthorityStatus{}, err
	}
	return s, nil
}
