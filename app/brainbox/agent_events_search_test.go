package brainbox

import (
	"net/url"
	"testing"
)

func TestSearchAgentEvents(t *testing.T) {
	client, cap := stubServer(t, 200, `{
		"items": [{"seq": 9, "id": "rule-exec:1", "type": "rule.execution",
		           "status": "failed", "ts": 5, "envelope": {"title": "x"}}],
		"backend": "opensearch", "total": 1}`)

	res, err := client.SearchAgentEvents(SearchAgentEventsOptions{
		Q: "boom & bust", Type: "rule.", Workspace: "personal", Limit: 50,
	})
	if err != nil {
		t.Fatal(err)
	}
	u, err := url.Parse(cap.path)
	if err != nil {
		t.Fatal(err)
	}
	if u.Path != "/api/agent_events/search" {
		t.Fatalf("unexpected path: %s", cap.path)
	}
	q := u.Query()
	if q.Get("q") != "boom & bust" { // free text must round-trip escaped
		t.Fatalf("q not escaped correctly: %s", cap.path)
	}
	if q.Get("type") != "rule." || q.Get("workspace") != "personal" || q.Get("limit") != "50" {
		t.Fatalf("bad query: %s", cap.path)
	}
	if q.Has("status") || q.Has("since_ms") {
		t.Fatalf("zero-value filters must be omitted: %s", cap.path)
	}
	if res.Backend != "opensearch" || len(res.Items) != 1 || res.Items[0].ID != "rule-exec:1" {
		t.Fatalf("bad decode: %+v", res)
	}
	if res.Total == nil || *res.Total != 1 {
		t.Fatalf("bad total: %+v", res.Total)
	}
}

func TestSearchAgentEventsPostgresNullTotal(t *testing.T) {
	client, _ := stubServer(t, 200, `{"items": [], "backend": "postgres", "total": null}`)
	res, err := client.SearchAgentEvents(SearchAgentEventsOptions{Q: "x"})
	if err != nil {
		t.Fatal(err)
	}
	if res.Backend != "postgres" || res.Total != nil {
		t.Fatalf("bad decode: %+v", res)
	}
}
