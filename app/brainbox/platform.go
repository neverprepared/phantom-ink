package brainbox

// Platform-services control plane — thin pass-throughs to the router's
// /api/platform surface. The router dispatches platform.ps/action work to the
// runner on the target node, which runs docker/`docker compose` there. This is
// how the app manages a REMOTE platform (not just a co-located one).

// PlatformNode is a docker-capable node that can host the platform stack.
type PlatformNode struct {
	Name string `json:"name"`
	Host string `json:"host"`
}

// PlatformNodesList is the GET /api/platform/nodes response.
type PlatformNodesList struct {
	Nodes []PlatformNode `json:"nodes"`
}

// RemotePlatformService is one compose service's live state, as parsed by the
// router from the runner's ps output. Mirrors the local PlatformService shape.
type RemotePlatformService struct {
	Name    string `json:"name"`
	State   string `json:"state"`
	Status  string `json:"status"`
	Health  string `json:"health"`
	OneShot bool   `json:"one_shot"`
	Addr    string `json:"addr"`
	WebURL  string `json:"web_url"`
}

// PlatformServicesResponse is the GET /api/platform/{node}/services response.
type PlatformServicesResponse struct {
	Node     string                  `json:"node"`
	Services []RemotePlatformService `json:"services"`
}

// PlatformActionResult is the POST /api/platform/{node}/action response.
type PlatformActionResult struct {
	OK      bool   `json:"ok"`
	Node    string `json:"node"`
	Action  string `json:"action"`
	Service string `json:"service"`
	Output  string `json:"output"`
}

// ListPlatformNodes returns docker-capable nodes eligible to host the platform.
func (c *Client) ListPlatformNodes() (PlatformNodesList, error) {
	var out PlatformNodesList
	err := c.get("/api/platform/nodes", &out)
	return out, err
}

// ListPlatformServicesOn returns the platform stack's live service status on a node.
func (c *Client) ListPlatformServicesOn(node string) (PlatformServicesResponse, error) {
	var out PlatformServicesResponse
	err := c.get("/api/platform/"+node+"/services", &out)
	return out, err
}

// PlatformActionOn runs up|stop|restart on a node — whole stack when service is "".
func (c *Client) PlatformActionOn(node, action, service string) (PlatformActionResult, error) {
	var out PlatformActionResult
	body := map[string]string{"action": action}
	if service != "" {
		body["service"] = service
	}
	err := c.post("/api/platform/"+node+"/action", body, &out)
	return out, err
}
