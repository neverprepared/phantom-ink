package main

import (
	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// NFSExport mirrors brainbox.NFSExport for the frontend.
type NFSExport struct {
	Path    string `json:"path"`
	Options string `json:"options"`
}

// ListNFSExports returns current NFS exports from the brainbox API.
func (a *App) ListNFSExports() ([]NFSExport, error) {
	exports, err := a.client.ListNFSExports()
	if err != nil {
		return nil, err
	}
	result := make([]NFSExport, len(exports))
	for i, e := range exports {
		result[i] = NFSExport{Path: e.Path, Options: e.Options}
	}
	return result, nil
}

// AddNFSExport adds a directory to /etc/exports. Opens a directory picker if path is empty.
func (a *App) AddNFSExport(path string) error {
	if path == "" {
		selected, err := runtime.OpenDirectoryDialog(a.ctx, runtime.OpenDialogOptions{
			Title: "Select directory to share via NFS",
		})
		if err != nil {
			return err
		}
		if selected == "" {
			return nil // user cancelled
		}
		path = selected
	}
	return a.client.AddNFSExport(path)
}

// RemoveNFSExport removes a directory from /etc/exports.
func (a *App) RemoveNFSExport(path string) error {
	return a.client.RemoveNFSExport(path)
}
