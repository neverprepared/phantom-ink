#!/bin/bash
# Build brainbox container image
# Usage: ./build.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$PROJECT_DIR")"

# Build the brainbox-mcp wheel on the host first. Its src tree contains a
# symlink (_credentials → ../../brainbox/src/brainbox/credentials) that escapes
# the docker COPY context; resolving it at host build time produces a
# self-contained wheel the image can install directly.
echo "Building brainbox-mcp wheel..."
rm -rf "$REPO_ROOT/packages/brainbox-mcp/dist"
( cd "$REPO_ROOT/packages/brainbox-mcp" && uv build --wheel )

echo "Building brainbox image..."
docker build -t brainbox -f "$REPO_ROOT/docker/brainbox/Dockerfile" "$REPO_ROOT"

echo "Done."
