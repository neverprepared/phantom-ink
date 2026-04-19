#!/bin/bash
# Build brainbox container image
# Usage: ./build.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$PROJECT_DIR")"

echo "Building brainbox..."
docker build -t brainbox -f "$REPO_ROOT/docker/brainbox/Dockerfile" "$REPO_ROOT" || exit 1

echo "Done."
