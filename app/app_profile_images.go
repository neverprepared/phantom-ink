package main

import (
	"fmt"
	"time"

	"phantom-ink/profileimage"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// ProfileImageBuildRequest specifies what to build.
type ProfileImageBuildRequest struct {
	Profile     string `json:"profile"`
	BaseImage   string `json:"base_image"`   // defaults to "brainbox" if empty
	RegistryURL string `json:"registry_url"` // overrides brainbox API registry if set
}

// ProfileImageBuildStatus is emitted as a runtime event during a build.
type ProfileImageBuildStatus struct {
	Profile string `json:"profile"`
	Step    string `json:"step"`
	Done    bool   `json:"done"`
	Error   string `json:"error,omitempty"`
	Tag     string `json:"tag,omitempty"`
	Digest  string `json:"digest,omitempty"`
}

// GetProfileImageInfo returns the locally-recorded state of a profile image.
func (a *App) GetProfileImageInfo(profileName string) (ProfileImageRow, bool) {
	if a.db == nil {
		return ProfileImageRow{}, false
	}
	return a.db.GetProfileImage(profileName)
}

// ListProfileImages returns all locally-recorded profile image records.
func (a *App) ListProfileImages() []ProfileImageRow {
	if a.db == nil {
		return nil
	}
	return a.db.ListProfileImages()
}

// GetRemoteProfileImageStatus queries the brainbox API to check whether a
// profile image exists in the configured registry.
func (a *App) GetRemoteProfileImageStatus(profileName string) (interface{}, error) {
	return a.client.GetProfileImageStatus(profileName)
}

// BuildProfileImage runs the full build pipeline for a profile and emits
// progress events. Returns when the build is complete or has failed.
//
// Progress events are emitted as "profile-image:progress" with ProfileImageBuildStatus payload.
func (a *App) BuildProfileImage(req ProfileImageBuildRequest) error {
	if req.Profile == "" {
		return fmt.Errorf("profile name is required")
	}

	prof, err := a.findProfile(req.Profile)
	if err != nil {
		return fmt.Errorf("find profile: %w", err)
	}

	baseImage := req.BaseImage
	if baseImage == "" {
		baseImage = "brainbox"
	}

	// Resolve registry URL: use request override, then fall back to API info.
	registryURL := req.RegistryURL
	if registryURL == "" {
		if status, err := a.client.GetProfileImageStatus(req.Profile); err == nil && status.Configured {
			// Extract registry URL from the tag (strip /brainbox-profile:name suffix)
			if tag := status.Tag; tag != "" {
				// tag = "registry/brainbox-profile:name" → registry = everything before /brainbox-profile
				if idx := len(tag) - len("/brainbox-profile:"+req.Profile); idx > 0 {
					registryURL = tag[:idx]
				}
			}
		}
	}
	if registryURL == "" {
		return fmt.Errorf("no registry URL configured — set CL_REGISTRY_URL on the brainbox server")
	}

	// Resolve registry credentials from DB settings.
	registryUsername := a.db.GetSetting(settingRegistryUsername, "")
	registryPassword := a.db.GetSetting(settingRegistryPassword, "")

	// Resolve 1Password vault from profile or global setting.
	opVault := a.db.GetSetting(settingOPVault, "")

	emit := func(step string, done bool, buildErr error, result *profileimage.BuildResult) {
		status := ProfileImageBuildStatus{
			Profile: req.Profile,
			Step:    step,
			Done:    done,
		}
		if buildErr != nil {
			status.Error = buildErr.Error()
		}
		if result != nil {
			status.Tag = result.Tag
			status.Digest = result.Digest
		}
		if a.ctx != nil {
			runtime.EventsEmit(a.ctx, "profile-image:progress", status)
		}
	}

	opts := profileimage.BuildOptions{
		Profile:          req.Profile,
		WorkspaceHome:    prof.WorkspaceHome,
		BaseImage:        baseImage,
		RegistryURL:      registryURL,
		RegistryUsername: registryUsername,
		RegistryPassword: registryPassword,
		OPVault:          opVault,
		Progress: func(msg string) {
			emit(msg, false, nil, nil)
		},
	}

	result, buildErr := profileimage.Build(opts)
	if buildErr != nil {
		emit("Build failed", true, buildErr, nil)
		return buildErr
	}

	// Persist record locally.
	if a.db != nil {
		_ = a.db.UpsertProfileImage(ProfileImageRow{
			Profile:      req.Profile,
			RegistryURL:  registryURL,
			LastPushedAt: time.Now().UTC().Format(time.RFC3339),
			LastDigest:   result.Digest,
		})
	}

	emit("Build complete", true, nil, &result)
	return nil
}

// DeleteProfileImageRecord removes the local DB record for a profile image.
// It does not delete the image from the registry.
func (a *App) DeleteProfileImageRecord(profileName string) error {
	if a.db == nil {
		return nil
	}
	return a.db.DeleteProfileImage(profileName)
}
