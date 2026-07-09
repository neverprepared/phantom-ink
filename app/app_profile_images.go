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
	NoCache     bool   `json:"no_cache"`     // drop local base image + re-pull before building
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

	// Resolve registry URL: use request override, then fall back to API info.
	registryURL := req.RegistryURL
	if registryURL == "" {
		registryURL = a.resolveRegistryURL(req.Profile)
	}
	if registryURL == "" {
		return fmt.Errorf("no registry URL configured — set CL_REGISTRY_URL on the brainbox server")
	}

	// Default base image to the registry-hosted brainbox image so the Mac
	// never needs a local copy — it pulls from the same registry it pushes to.
	baseImage := req.BaseImage
	if baseImage == "" {
		baseImage = registryURL + "/brainbox:latest"
	}

	// Resolve registry credentials from DB settings.
	registryUsername := a.db.GetSetting(settingRegistryUsername, "")
	registryPassword := a.db.GetSetting(settingRegistryPassword, "")

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
		OTLPHost:         a.db.GetSetting(settingOTLPHost, ""),
		NoCache:          req.NoCache,
		Progress: func(msg string) {
			emit(msg, false, nil, nil)
		},
	}

	result, buildErr := profileimage.Build(opts)
	if buildErr != nil {
		emit("Build failed", true, buildErr, nil)
		return buildErr
	}

	// Persist record locally (including the env decryption key).
	if a.db != nil {
		_ = a.db.UpsertProfileImage(ProfileImageRow{
			Profile:      req.Profile,
			RegistryURL:  registryURL,
			LastPushedAt: time.Now().UTC().Format(time.RFC3339),
			LastDigest:   result.Digest,
			EnvKey:       result.EnvKey,
		})
	}

	// Credential-bundle sync rides along with the rebuild (warn-and-continue:
	// a MinIO/daemon hiccup must never fail an otherwise good image build).
	// Only runs when the profile has sources enabled and MinIO is reachable.
	anyEnabled := false
	if a.db != nil {
		for _, r := range a.db.GetBundleSources(req.Profile) {
			if r.Enabled {
				anyEnabled = true
				break
			}
		}
	}
	if anyEnabled {
		emit("Syncing credential bundle…", false, nil, nil)
		if _, err := a.syncProfileBundle(req.Profile, prof.WorkspaceHome, func(msg string) {
			emit(msg, false, nil, nil)
		}); err != nil {
			emit(fmt.Sprintf("warning: credential bundle sync failed: %v", err), false, nil, nil)
		}
	}

	emit("Build complete", true, nil, &result)
	return nil
}

// resolveRegistryURL derives the private registry base URL from the profile's
// image status (the tag is "<registry>/brainbox-profile:<name>"). Returns ""
// when no registry is configured.
func (a *App) resolveRegistryURL(profile string) string {
	status, err := a.client.GetProfileImageStatus(profile)
	if err != nil || !status.Configured || status.Tag == "" {
		return ""
	}
	if idx := len(status.Tag) - len("/brainbox-profile:"+profile); idx > 0 {
		return status.Tag[:idx]
	}
	return ""
}

// BaseImageBuildRequest specifies a base brainbox image rebuild.
type BaseImageBuildRequest struct {
	Profile string `json:"profile"`  // used to resolve repo root + registry
	NoCache bool   `json:"no_cache"` // pass --no-cache to docker build
}

// RebuildBaseImage rebuilds the brainbox base image (the layer holding
// ttyd-wrapper.sh and the Dockerfile config) and pushes it to the registry as
// "<registry>/brainbox:latest". This is the upstream half of the chain: a
// profile rebuild only picks up Dockerfile/script changes after the base is
// rebuilt and pushed here. Progress streams as "base-image:progress" events.
func (a *App) RebuildBaseImage(req BaseImageBuildRequest) error {
	if req.Profile == "" {
		return fmt.Errorf("profile name is required to locate the repo and registry")
	}
	prof, err := a.findProfile(req.Profile)
	if err != nil {
		return fmt.Errorf("find profile: %w", err)
	}

	registryURL := a.resolveRegistryURL(req.Profile)
	if registryURL == "" {
		return fmt.Errorf("no registry URL configured — set CL_REGISTRY_URL on the brainbox server")
	}

	emit := func(step string, done bool, buildErr error) {
		status := ProfileImageBuildStatus{Profile: req.Profile, Step: step, Done: done}
		if buildErr != nil {
			status.Error = buildErr.Error()
		}
		if a.ctx != nil {
			runtime.EventsEmit(a.ctx, "base-image:progress", status)
		}
	}

	buildErr := profileimage.BuildBase(profileimage.BaseBuildOptions{
		RepoRoot:         prof.WorkspaceHome + "/code/phantom-ink",
		RegistryURL:      registryURL,
		RegistryUsername: a.db.GetSetting(settingRegistryUsername, ""),
		RegistryPassword: a.db.GetSetting(settingRegistryPassword, ""),
		NoCache:          req.NoCache,
		Progress:         func(msg string) { emit(msg, false, nil) },
	})
	if buildErr != nil {
		emit("Base build failed", true, buildErr)
		return buildErr
	}
	emit("Base image rebuilt and pushed", true, nil)
	return nil
}

// GetProfileEnvKey returns the AES key for a profile's encrypted .env.enc,
// or an empty string if none is stored. Used to pass PROFILE_ENV_KEY when
// creating sessions so the container can decrypt its environment.
func (a *App) GetProfileEnvKey(profileName string) string {
	if a.db == nil {
		return ""
	}
	row, ok := a.db.GetProfileImage(profileName)
	if !ok {
		return ""
	}
	return row.EnvKey
}

// DeleteProfileImageRecord removes the local DB record for a profile image.
// It does not delete the image from the registry.
func (a *App) DeleteProfileImageRecord(profileName string) error {
	if a.db == nil {
		return nil
	}
	return a.db.DeleteProfileImage(profileName)
}
