#!/bin/bash
# Build brainbox container image
#
# Usage: ./build.sh
#
# Environment:
#   REGISTRY_URL   When set, the built image is tagged and pushed as
#                  "$REGISTRY_URL/brainbox:latest" after a successful build.
#                  (docker login must already be done — the Wails app handles
#                  this before invoking the script.)
#   NO_CACHE=1     Pass --no-cache to docker build, forcing every layer to
#                  rebuild from scratch. Normally unnecessary — COPY layers
#                  auto-invalidate when their source files change — but useful
#                  when an upstream apt/brew package needs a clean pull.
#   PHANTOM_BRAIN_DIR / PROUTERCTL_DIR
#                  Local checkouts of the private neverprepared repos whose CLIs
#                  (pbrainctl, prouterctl) are built from source in Dockerfile
#                  builder stages and copied into the image. Default: sibling
#                  dirs next to this repo (…/code/phantom-brain, …/code/prouterctl).
set -euo pipefail

# Named build contexts require BuildKit; export so GUI/launchd callers (the
# Wails app) that don't set it still build correctly.
export DOCKER_BUILDKIT=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$PROJECT_DIR")"
CODE_ROOT="$(dirname "$REPO_ROOT")"

# Local checkouts of the private CLIs, built from source (no PAT, no public
# hosting). Overridable; default to siblings of this repo.
PHANTOM_BRAIN_DIR="${PHANTOM_BRAIN_DIR:-$CODE_ROOT/phantom-brain}"
PROUTERCTL_DIR="${PROUTERCTL_DIR:-$CODE_ROOT/prouterctl}"
for d in "$PHANTOM_BRAIN_DIR" "$PROUTERCTL_DIR"; do
    if [ ! -f "$d/go.mod" ]; then
        echo "ERROR: expected a Go checkout at '$d' (go.mod not found)." >&2
        echo "       Set PHANTOM_BRAIN_DIR / PROUTERCTL_DIR to the local repo paths." >&2
        exit 1
    fi
done
# Stamp each binary with the checkout's git description (falls back to 'container').
PBRAINCTL_VERSION="$(git -C "$PHANTOM_BRAIN_DIR" describe --tags --always --dirty 2>/dev/null || echo container)"

# Build the brainbox-mcp wheel on the host first. Its src tree contains a
# symlink (_credentials → ../../brainbox/src/brainbox/credentials) that escapes
# the docker COPY context; resolving it at host build time produces a
# self-contained wheel the image can install directly.
echo "Building brainbox-mcp wheel..."
rm -rf "$REPO_ROOT/packages/brainbox-mcp/dist"
( cd "$REPO_ROOT/packages/brainbox-mcp" && uv build --wheel )

BUILD_ARGS=()
if [ "${NO_CACHE:-}" = "1" ]; then
    BUILD_ARGS+=(--no-cache)
    echo "Building brainbox image (--no-cache)..."
else
    echo "Building brainbox image..."
fi
# ${arr[@]+"${arr[@]}"} guards the empty-array expansion: under set -u,
# bash 3.2 (macOS default) treats "${BUILD_ARGS[@]}" on an empty array as an
# unbound variable and aborts. The +alternate form expands to nothing when the
# array is empty and to the quoted elements otherwise.
docker build ${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"} \
    --build-context "phantombrain=$PHANTOM_BRAIN_DIR" \
    --build-context "prouterctl=$PROUTERCTL_DIR" \
    --build-arg "PBRAINCTL_VERSION=$PBRAINCTL_VERSION" \
    -t brainbox -f "$REPO_ROOT/docker/brainbox/Dockerfile" "$REPO_ROOT"

if [ -n "${REGISTRY_URL:-}" ]; then
    REMOTE_TAG="${REGISTRY_URL%/}/brainbox:latest"
    echo "Tagging and pushing $REMOTE_TAG ..."
    docker tag brainbox "$REMOTE_TAG"
    docker push "$REMOTE_TAG"
fi

echo "Done."
