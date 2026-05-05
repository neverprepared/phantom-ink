package brainbox

import "net/url"

// NFSExport represents a single entry from /etc/exports.
type NFSExport struct {
	Path    string `json:"path"`
	Options string `json:"options"`
}

// ListNFSExports returns current NFS exports from the brainbox API.
func (c *Client) ListNFSExports() ([]NFSExport, error) {
	var exports []NFSExport
	err := c.get("/api/nfs/exports", &exports)
	return exports, err
}

// AddNFSExport adds a directory to /etc/exports via the brainbox API.
func (c *Client) AddNFSExport(path string) error {
	var resp map[string]interface{}
	return c.post("/api/nfs/exports", map[string]string{"path": path}, &resp)
}

// RemoveNFSExport removes a directory from /etc/exports via the brainbox API.
func (c *Client) RemoveNFSExport(path string) error {
	var resp map[string]interface{}
	return c.delete("/api/nfs/exports?path="+url.QueryEscape(path), &resp)
}
