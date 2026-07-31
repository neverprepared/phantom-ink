package brainbox

import (
	"fmt"
	"net/url"
)

// Pool is a machine-class routing pool (owned by the fleet, proxied via the
// router). Membership is implicit: a runner is in the pool when its tags are a
// superset of MatchTags. Policy is "strict" (never spill) or "spill".
type Pool struct {
	Name      string   `json:"name"`
	MatchTags []string `json:"match_tags"`
	Policy    string   `json:"policy"`
}

// ListPools returns the machine-class routing pools (empty if no fleet).
func (c *Client) ListPools() ([]Pool, error) {
	var resp struct {
		Pools []Pool `json:"pools"`
	}
	if err := c.get("/api/pools", &resp); err != nil {
		return nil, err
	}
	return resp.Pools, nil
}

// UpsertPool creates or updates a pool's match tags + policy.
func (c *Client) UpsertPool(name string, matchTags []string, policy string) (Pool, error) {
	body := map[string]any{"match_tags": matchTags, "policy": policy}
	var p Pool
	if err := c.put(fmt.Sprintf("/api/pools/%s", url.PathEscape(name)), body, &p); err != nil {
		return Pool{}, err
	}
	return p, nil
}

// DeletePool removes a pool (membership is by tags, so runners are unaffected).
func (c *Client) DeletePool(name string) error {
	return c.delete(fmt.Sprintf("/api/pools/%s", url.PathEscape(name)), nil)
}
