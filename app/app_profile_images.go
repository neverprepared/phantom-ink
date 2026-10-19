package main

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"phantom-ink/profileimage"
	"phantom-ink/provider"

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

// ProfileImageState is why a profile's image badge looks the way it does.
//
// The four failure modes used to be indistinguishable. The old binding was an
// untyped passthrough returning interface{}, and ProfilesPanel caught ANY
// throw into `{configured:false, exists:false}` -- so a registry 401 rendered
// exactly like "no image configured". That is the recorded registry-auth
// gotcha ("the app 'image built' badge needs REGISTRY_PASSWORD in the m3 .env,
// else 401 reads as 'no image'"), and it was unfixable in the UI because the
// Go signature carried no reason to branch on.
type ProfileImageState string

const (
	// ImageStateUnconfigured: no registry is set up for this profile. Not an
	// error -- there is nothing to look for.
	ImageStateUnconfigured ProfileImageState = "unconfigured"
	// ImageStateUnauthorized: the registry rejected our credential (401/403).
	// Actionable, and the one that used to masquerade as "missing".
	ImageStateUnauthorized ProfileImageState = "unauthorized"
	// ImageStateUnreachable: the registry or the router could not be reached.
	ImageStateUnreachable ProfileImageState = "unreachable"
	// ImageStateMissing: registry reachable and authorized, no image yet.
	ImageStateMissing ProfileImageState = "missing"
	// ImageStatePresent: the image is there.
	ImageStatePresent ProfileImageState = "present"
)

// ProfileImageStatusView is the typed status one profile's badge renders from.
type ProfileImageStatusView struct {
	Profile string            `json:"profile"`
	State   ProfileImageState `json:"state"`
	// Configured and Exists are kept so existing callers keep working, but
	// State is the field to branch on.
	Configured bool   `json:"configured"`
	Exists     bool   `json:"exists"`
	Tag        string `json:"tag"`
	Digest     string `json:"digest"`
	// Detail carries the underlying message for the unauthorized/unreachable
	// states, so the UI can show what to fix rather than just that it broke.
	Detail string `json:"detail"`
}

// GetRemoteProfileImageStatus queries the brainbox API for one profile's image.
func (a *App) GetRemoteProfileImageStatus(profileName string) (ProfileImageStatusView, error) {
	return a.profileImageStatus(profileName), nil
}

// GetRemoteProfileImageStatuses queries several profiles concurrently.
//
// ProfilesPanel used to fire one un-awaited call per profile in a bare `for`
// loop -- 5 + N round trips per panel load, N of them remote registry hits,
// unordered, with each failure folded into "not configured".
func (a *App) GetRemoteProfileImageStatuses(profiles []string) (map[string]ProfileImageStatusView, error) {
	out := make(map[string]ProfileImageStatusView, len(profiles))
	if len(profiles) == 0 {
		return out, nil
	}

	var (
		mu sync.Mutex
		wg sync.WaitGroup
	)
	sem := make(chan struct{}, maxConcurrentReads)
	for _, p := range profiles {
		if strings.TrimSpace(p) == "" {
			continue
		}
		wg.Add(1)
		go func(profile string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			view := a.profileImageStatus(profile)
			mu.Lock()
			out[profile] = view
			mu.Unlock()
		}(p)
	}
	wg.Wait()
	return out, nil
}

// profileImageStatus maps the remote call's outcome onto a state. Never returns
// an error: every outcome is a renderable state, which is the whole point.
func (a *App) profileImageStatus(profileName string) ProfileImageStatusView {
	view := ProfileImageStatusView{Profile: profileName, State: ImageStateUnreachable}
	if a.client == nil {
		view.Detail = "brainbox client not configured"
		return view
	}

	status, err := a.client.GetProfileImageStatus(profileName)
	if err != nil {
		view.State = classifyRegistryError(err)
		view.Detail = err.Error()
		return view
	}

	view.Configured = status.Configured
	view.Exists = status.Exists
	view.Tag = status.Tag
	view.Digest = status.Digest

	switch {
	case status.Error != "":
		// The router reached the registry and the registry complained.
		view.State = classifyRegistryMessage(status.Error)
		view.Detail = status.Error
	case !status.Configured:
		view.State = ImageStateUnconfigured
	case status.Exists:
		view.State = ImageStatePresent
	default:
		view.State = ImageStateMissing
	}
	return view
}

// classifyRegistryError distinguishes an auth rejection from a reachability
// failure, so the badge can say which.
func classifyRegistryError(err error) ProfileImageState {
	if provider.IsUnauthorized(err) {
		return ImageStateUnauthorized
	}
	return classifyRegistryMessage(err.Error())
}

// classifyRegistryMessage inspects a message for an auth signal. String
// matching is a last resort -- the router returns the registry's message as
// free text -- so it is deliberately narrow and defaults to unreachable.
func classifyRegistryMessage(msg string) ProfileImageState {
	l := strings.ToLower(msg)
	switch {
	case strings.Contains(l, "401"), strings.Contains(l, "403"),
		strings.Contains(l, "unauthorized"), strings.Contains(l, "forbidden"),
		strings.Contains(l, "authentication required"), strings.Contains(l, "denied"):
		return ImageStateUnauthorized
	default:
		return ImageStateUnreachable
	}
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

	// Publish the env key to the broker so ROUTER-created (task/hub) sessions
	// can decrypt this image's baked credentials — app-created sessions get it
	// from the local DB, but the router has no other source. Each build mints a
	// new key, so this must run every build. Warn-and-continue: a broker hiccup
	// must not fail an otherwise-good image build.
	if result.EnvKey != "" {
		if err := a.client.SetGatewayProfileEnvKey(req.Profile, "PROFILE_ENV_KEY", result.EnvKey); err != nil {
			runtime.LogWarningf(a.ctx, "profile image: failed to publish PROFILE_ENV_KEY to broker: %v", err)
		}
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
	// An explicit app setting wins — it's what the operator configured in
	// Settings and what the Platform Services indicator health-checks.
	if a.db != nil {
		if url := a.db.GetSetting(settingRegistryURL, ""); url != "" {
			return url
		}
	}
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

// DeleteProfileImageRecord removes the local DB record for a profile image.
// It does not delete the image from the registry.
func (a *App) DeleteProfileImageRecord(profileName string) error {
	if a.db == nil {
		return nil
	}
	return a.db.DeleteProfileImage(profileName)
}
