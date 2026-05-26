"""Input validation for brainbox API."""

import re
from pathlib import Path
from typing import Tuple


class ValidationError(Exception):
    """Raised when input validation fails."""

    pass


def validate_session_name(name: str) -> str:
    """
    Validate session name follows Docker container naming rules.

    Rules:
    - Must start with alphanumeric character
    - Can contain: letters, numbers, underscore, hyphen, dot
    - Must be 1-64 characters long
    - No path traversal (no .. sequences)

    Args:
        name: Session name to validate

    Returns:
        The validated name (unchanged)

    Raises:
        ValidationError: If name is invalid
    """
    if not name:
        raise ValidationError("Session name cannot be empty")

    if len(name) > 64:
        raise ValidationError(f"Session name too long (max 64 chars): {len(name)}")

    # Docker naming rules: must start with alphanumeric, then [a-zA-Z0-9_.-]
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$", name):
        raise ValidationError(
            f"Invalid session name '{name}': must start with alphanumeric and "
            "contain only letters, numbers, underscore, hyphen, and dot"
        )

    # Prevent path traversal
    if ".." in name:
        raise ValidationError(f"Invalid session name '{name}': cannot contain '..'")

    return name


def validate_volume_mount(volume_spec: str) -> Tuple[str, str, str]:
    """
    Validate and parse volume mount specification.

    Format: host_path:container_path[:mode]
    - host_path: must be absolute
    - container_path: must be absolute
    - mode: must be 'ro' or 'rw' (default: 'rw')

    Args:
        volume_spec: Volume mount specification string

    Returns:
        Tuple of (host_path, container_path, mode)

    Raises:
        ValidationError: If volume_spec is invalid
    """
    if not volume_spec:
        raise ValidationError("Volume mount specification cannot be empty")

    parts = volume_spec.split(":")
    if len(parts) < 2:
        raise ValidationError(
            f"Invalid volume format '{volume_spec}': expected 'host:container[:mode]'"
        )

    host_path = parts[0]
    container_path = parts[1]
    mode = parts[2] if len(parts) > 2 else "rw"

    # Validate host path
    if not host_path:
        raise ValidationError("Host path cannot be empty")

    # Host path must be absolute
    host_path_obj = Path(host_path)
    if not host_path_obj.is_absolute():
        raise ValidationError(f"Host path must be absolute: '{host_path}'")

    # Prevent path traversal
    if ".." in host_path_obj.parts:
        raise ValidationError(f"Host path cannot contain '..': '{host_path}'")

    # Deny-list: sensitive host paths that should never be mounted
    _DENIED_HOST_PATHS = {
        "/",
        "/etc",
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/var/run",
        "/proc",
        "/sys",
        "/dev",
        "/boot",
        "/root",
    }
    resolved = str(host_path_obj.resolve())
    if resolved in _DENIED_HOST_PATHS:
        raise ValidationError(f"Host path is denied for security: '{resolved}'")

    # Validate container path
    if not container_path:
        raise ValidationError("Container path cannot be empty")

    container_path_obj = Path(container_path)
    if not container_path_obj.is_absolute():
        raise ValidationError(f"Container path must be absolute: '{container_path}'")

    # Validate mode
    if mode not in ("rw", "ro"):
        raise ValidationError(
            f"Invalid volume mode '{mode}': must be 'rw' (read-write) or 'ro' (read-only)"
        )

    return str(host_path_obj), str(container_path_obj), mode


def validate_port(port: int) -> int:
    """
    Validate port number is in valid range.

    Args:
        port: Port number to validate

    Returns:
        The validated port number

    Raises:
        ValidationError: If port is out of range
    """
    if not isinstance(port, int):
        raise ValidationError(f"Port must be an integer, got {type(port).__name__}")

    if not 1024 <= port <= 65535:
        raise ValidationError(f"Port {port} out of range (must be 1024-65535 for non-root)")

    return port


def validate_role(role: str) -> str:
    """
    Validate role against registered agents.

    Args:
        role: Role to validate

    Returns:
        The validated role

    Raises:
        ValidationError: If role is not a known agent name
    """
    # Import here to avoid circular imports at module load time
    from . import registry  # noqa: PLC0415

    known = {ag.name for ag in registry.list_agents()}
    if not known:
        # Registry not yet loaded — fall back to permissive check
        return role
    if role not in known:
        raise ValidationError(
            f"Invalid role '{role}': must be one of {', '.join(sorted(known))}"
        )

    return role
