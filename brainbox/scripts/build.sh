#!/bin/bash
# Build brainbox container images
# Usage: ./build.sh [developer|researcher|performer|all]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$PROJECT_DIR")"
ROLE="${1:-all}"

build_base() {
    echo "Building brainbox-base..."
    docker build -t brainbox-base -f "$REPO_ROOT/docker/brainbox/Dockerfile" "$REPO_ROOT" || exit 1
}

build_role() {
    local role="$1"
    local image_name="brainbox-${role}"
    local dockerfile="$REPO_ROOT/docker/brainbox/Dockerfile.${role}"

    if [ ! -f "$dockerfile" ]; then
        echo "Error: Dockerfile not found: $dockerfile"
        exit 1
    fi

    echo "Building ${image_name}..."
    docker build -t "$image_name" -f "$dockerfile" "$REPO_ROOT" || exit 1

    # Remove old container so run.sh creates a fresh one from the new image
    local container_name="${role}"
    if docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        echo "Removing old container ${container_name}..."
        docker rm -f "$container_name" > /dev/null
    fi
}

# Always build base first
build_base

case "$ROLE" in
    developer|researcher|performer)
        build_role "$ROLE"
        ;;
    all)
        build_role developer
        build_role researcher
        build_role performer
        ;;
    *)
        echo "Usage: $0 [developer|researcher|performer|all]"
        exit 1
        ;;
esac

echo "Done."
