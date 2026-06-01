package brainbox

import "fmt"

// ProfileImageStatus is returned by GET /api/profiles/{name}/image/status.
type ProfileImageStatus struct {
	Configured bool   `json:"configured"`
	Profile    string `json:"profile"`
	Exists     bool   `json:"exists"`
	Tag        string `json:"tag"`
	Digest     string `json:"digest"`
	Error      string `json:"error"`
}

// GetProfileImageStatus checks whether a profile image exists in the registry.
func (c *Client) GetProfileImageStatus(profile string) (ProfileImageStatus, error) {
	var status ProfileImageStatus
	if err := c.get(fmt.Sprintf("/api/profiles/%s/image/status", profile), &status); err != nil {
		return ProfileImageStatus{}, err
	}
	return status, nil
}
