package brainbox

// Integrations control plane (ADR-003) — thin pass-throughs to the router's
// /api/integrations surface. The router places compose stacks on fleet nodes;
// the app wires the returned endpoint into host consumer config.

// IntegrationConsumer declares where an integration's endpoint should be wired.
// target=host → the app writes the profile's MCP config; target=gateway → the
// router writes the gateway catalog.
type IntegrationConsumer struct {
	Target string `json:"target"`
	Server string `json:"server"`
	Env    string `json:"env"`
}

// IntegrationPlacement is the durable desired placement of an integration.
type IntegrationPlacement struct {
	Name      string `json:"name"`
	Node      string `json:"node"`
	Desired   string `json:"desired"`
	UpdatedAt int64  `json:"updated_at"`
}

// Integration is one catalog entry with its current placement + endpoint.
type Integration struct {
	Name        string                `json:"name"`
	Description string                `json:"description"`
	Capability  string                `json:"capability"`
	Consumers   []IntegrationConsumer `json:"consumers"`
	Placement   *IntegrationPlacement `json:"placement"`
	Endpoint    string                `json:"endpoint"`
}

// IntegrationNode is a fleet node eligible to host an integration.
type IntegrationNode struct {
	Name string `json:"name"`
	Host string `json:"host"`
}

// IntegrationsList is the GET /api/integrations response.
type IntegrationsList struct {
	Integrations []Integration     `json:"integrations"`
	Nodes        []IntegrationNode `json:"nodes"`
}

// IntegrationPlacementResult is the POST placement response.
type IntegrationPlacementResult struct {
	OK       bool   `json:"ok"`
	Name     string `json:"name"`
	Node     string `json:"node"`
	Desired  string `json:"desired"`
	Endpoint string `json:"endpoint"`
	Output   string `json:"output"`
}

// ListIntegrations returns the catalog + placements + eligible nodes.
func (c *Client) ListIntegrations() (IntegrationsList, error) {
	var out IntegrationsList
	err := c.get("/api/integrations", &out)
	return out, err
}

// SetIntegrationPlacement places (desired="on") or removes (desired="off") an
// integration on a node and returns the reachable endpoint.
func (c *Client) SetIntegrationPlacement(name, node, desired string) (IntegrationPlacementResult, error) {
	var out IntegrationPlacementResult
	body := map[string]string{"node": node, "desired": desired}
	err := c.post("/api/integrations/"+name+"/placement", body, &out)
	return out, err
}
