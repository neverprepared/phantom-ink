package jira

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Issue is the flattened subset of a Jira issue the Code panel renders.
type Issue struct {
	Key            string `json:"key"`
	Summary        string `json:"summary"`
	Status         string `json:"status"`
	StatusCategory string `json:"status_category"` // new | indeterminate | done
	Assignee       string `json:"assignee"`
	URL            string `json:"url"`
}

// Config is the one app-level credential set. Not per profile: profiles opt in
// to using it, they do not each carry their own copy.
type Config struct {
	BaseURL  string
	Username string
	Token    string
}

type Client struct {
	cfg      Config
	hc       *http.Client
	issues   *cache[Issue]
	projects *cache[map[string]bool]
	searches *cache[[]Issue]
}

func New(cfg Config, hc *http.Client) *Client {
	if hc == nil {
		hc = &http.Client{Timeout: 15 * time.Second}
	}
	cfg.BaseURL = strings.TrimRight(strings.TrimSpace(cfg.BaseURL), "/")
	return &Client{
		cfg:      cfg,
		hc:       hc,
		issues:   newCache[Issue](5 * time.Minute),
		projects: newCache[map[string]bool](24 * time.Hour),
		searches: newCache[[]Issue](2 * time.Minute),
	}
}

func (c *Client) do(ctx context.Context, method, path string, body any, out any) error {
	var r *bytes.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		r = bytes.NewReader(b)
	} else {
		r = bytes.NewReader(nil)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.cfg.BaseURL+path, r)
	if err != nil {
		return err
	}
	req.SetBasicAuth(c.cfg.Username, c.cfg.Token)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return fmt.Errorf("jira %s %s: %s", method, path, resp.Status)
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// Verify checks the credentials. Used by the Integrations status probe in place
// of the compose-ps check a hosted integration would run.
func (c *Client) Verify(ctx context.Context) error {
	return c.do(ctx, http.MethodGet, "/rest/api/3/myself", nil, nil)
}

// ProjectKeys returns the set of real project keys, cached for 24h.
func (c *Client) ProjectKeys(ctx context.Context) (map[string]bool, error) {
	if v, ok := c.projects.get("all"); ok {
		return v, nil
	}
	var raw []struct {
		Key string `json:"key"`
	}
	if err := c.do(ctx, http.MethodGet, "/rest/api/3/project", nil, &raw); err != nil {
		return nil, err
	}
	set := make(map[string]bool, len(raw))
	for _, p := range raw {
		set[p.Key] = true
	}
	c.projects.put("all", set)
	return set, nil
}

// Resolve maps issue keys to issues. Keys from unknown projects are dropped,
// cached issues are served locally, and whatever remains is fetched in ONE
// batched JQL call.
func (c *Client) Resolve(ctx context.Context, keys []string) (map[string]Issue, error) {
	out := make(map[string]Issue)
	if len(keys) == 0 {
		return out, nil
	}
	known, err := c.ProjectKeys(ctx)
	if err != nil {
		return nil, err
	}
	var want []string
	for _, k := range FilterKeys(keys, known) {
		if iss, ok := c.issues.get(k); ok {
			out[k] = iss
			continue
		}
		want = append(want, k)
	}
	if len(want) == 0 {
		return out, nil
	}
	body := map[string]any{
		"jql":        fmt.Sprintf("key in (%s)", strings.Join(want, ",")),
		"fields":     []string{"summary", "status", "assignee"},
		"maxResults": len(want),
	}
	found, err := c.search(ctx, body)
	if err != nil {
		return nil, err
	}
	for _, iss := range found {
		c.issues.put(iss.Key, iss)
		out[iss.Key] = iss
	}
	return out, nil
}

// searchResponse is the wire shape of a JQL search; Resolve and Search share it.
type searchResponse struct {
	Issues []struct {
		Key    string `json:"key"`
		Fields struct {
			Summary string `json:"summary"`
			Status  struct {
				Name           string `json:"name"`
				StatusCategory struct {
					Key string `json:"key"`
				} `json:"statusCategory"`
			} `json:"status"`
			Assignee struct {
				DisplayName string `json:"displayName"`
			} `json:"assignee"`
		} `json:"fields"`
	} `json:"issues"`
}

func (c *Client) search(ctx context.Context, body map[string]any) ([]Issue, error) {
	var raw searchResponse
	if err := c.do(ctx, http.MethodPost, "/rest/api/3/search/jql", body, &raw); err != nil {
		return nil, err
	}
	out := make([]Issue, 0, len(raw.Issues))
	for _, i := range raw.Issues {
		out = append(out, Issue{
			Key:            i.Key,
			Summary:        i.Fields.Summary,
			Status:         i.Fields.Status.Name,
			StatusCategory: i.Fields.Status.StatusCategory.Key,
			Assignee:       i.Fields.Assignee.DisplayName,
			URL:            c.cfg.BaseURL + "/browse/" + i.Key,
		})
	}
	return out, nil
}

// Search runs an operator-supplied JQL query and returns the matching issues in
// Jira's own order (the JQL's ORDER BY), which a map could not preserve.
//
// The results are cached under the query string: the ticket list is a second
// Jira call on every panel load, and the JQL rarely changes between them.
//
// An empty query is refused rather than sent: Jira treats it as "everything",
// which would pull the whole instance.
func (c *Client) Search(ctx context.Context, jql string, max int) ([]Issue, error) {
	jql = strings.TrimSpace(jql)
	if jql == "" {
		return nil, errors.New("jira: empty JQL")
	}
	if max <= 0 || max > 100 {
		max = 50
	}
	if v, ok := c.searches.get(jql); ok {
		return v, nil
	}
	found, err := c.search(ctx, map[string]any{
		"jql":        jql,
		"fields":     []string{"summary", "status", "assignee"},
		"maxResults": max,
	})
	if err != nil {
		return nil, err
	}
	// Individual issues warm the per-key cache too, so opening the Jira tab
	// makes the Code tab's chips free.
	for _, iss := range found {
		c.issues.put(iss.Key, iss)
	}
	c.searches.put(jql, found)
	return found, nil
}
