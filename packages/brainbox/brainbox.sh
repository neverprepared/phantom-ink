#!/bin/bash
# brainbox — Docker Compose wrapper
set -e

COMPOSE_FILE="/usr/share/brainbox/docker-compose.yml"
BRAINBOX_VERSION="@@VERSION@@"

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed." >&2
    exit 1
fi

# Check if Docker daemon is running
if ! docker info &> /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start the Docker daemon." >&2
    exit 1
fi

case "$1" in
  up|start)
    exec docker compose -f "$COMPOSE_FILE" up -d
    ;;
  down|stop)
    exec docker compose -f "$COMPOSE_FILE" down
    ;;
  logs)
    exec docker compose -f "$COMPOSE_FILE" logs -f
    ;;
  status|ps)
    exec docker compose -f "$COMPOSE_FILE" ps
    ;;
  pull)
    exec docker compose -f "$COMPOSE_FILE" pull
    ;;
  version|--version|-v)
    echo "brainbox $BRAINBOX_VERSION"
    ;;
  *)
    echo "Usage: brainbox {up|start|down|stop|logs|status|pull|version}"
    echo ""
    echo "Commands:"
    echo "  up/start   Start brainbox stack (API + Qdrant)"
    echo "  down/stop  Stop brainbox stack"
    echo "  logs       Follow logs from all services"
    echo "  status     Show running containers"
    echo "  pull       Pull latest images"
    echo "  version    Show brainbox version"
    exit 1
    ;;
esac
