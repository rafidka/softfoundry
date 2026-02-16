"""Agent status file management for monitoring and coordination.

Status files are stored at ~/.softfoundry/agents/{prefix}/ and contain
JSON data about each agent's current state, enabling monitoring of
agent health and coordination between agents.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

STATUS_DIR = Path.home() / ".softfoundry" / "agents"
STALE_THRESHOLD_SECONDS = 300  # 5 minutes


def sanitize_name(name: str) -> str:
    """Convert a name to a safe filename slug.

    Args:
        name: The name to sanitize (e.g., "Alice Chen").

    Returns:
        A sanitized lowercase string (e.g., "alice-chen").
    """
    sanitized = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return sanitized.strip("-")


def get_status_path(
    prefix: str, agent_type: str, agent_name: str | None = None
) -> Path:
    """Get path to an agent's status file.

    Args:
        prefix: Namespace for organizing status files (e.g., project name).
        agent_type: Type of agent ("manager", "programmer", "reviewer").
        agent_name: Agent name (e.g., "Alice Chen"). If provided and different
            from agent_type, it's included in the filename.

    Returns:
        Path to the status file.
    """
    dir_path = STATUS_DIR / prefix
    dir_path.mkdir(parents=True, exist_ok=True)

    # Include agent_name in filename if it's different from agent_type
    if agent_name and agent_name != agent_type and agent_name != "default":
        filename = f"{agent_type}-{sanitize_name(agent_name)}.status"
    else:
        filename = f"{agent_type}.status"

    return dir_path / filename


def update_status(
    status_path: Path,
    status: str,
    details: str = "",
    agent_type: str | None = None,
    name: str | None = None,
    project: str | None = None,
    current_issue: int | None = None,
    current_pr: int | None = None,
    **extra: Any,
) -> None:
    """Update an agent's status file.

    Args:
        status_path: Path to the status file.
        status: Current status (e.g., "working", "idle", "exited:success").
        details: Human-readable description of current activity.
        agent_type: Type of agent (preserved from existing if not provided).
        name: Agent name (preserved from existing if not provided).
        project: Project name (preserved from existing if not provided).
        current_issue: Issue number being worked on (optional).
        current_pr: PR number created (optional).
        **extra: Additional fields to include.
    """
    # Read existing data to preserve fields
    existing: dict[str, Any] = {}
    if status_path.exists():
        try:
            existing = json.loads(status_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Build updated data, preserving existing fields
    data: dict[str, Any] = {
        **existing,
        "status": status,
        "details": details,
        "last_update": datetime.now().isoformat(),
        "pid": os.getpid(),
        **extra,
    }

    # Update optional fields if provided
    if agent_type is not None:
        data["agent_type"] = agent_type
    if name is not None:
        data["name"] = name
    if project is not None:
        data["project"] = project
    if current_issue is not None:
        data["current_issue"] = current_issue
    elif "current_issue" not in data:
        data["current_issue"] = None
    if current_pr is not None:
        data["current_pr"] = current_pr
    elif "current_pr" not in data:
        data["current_pr"] = None

    # Set started_at if not already set
    if "started_at" not in data:
        data["started_at"] = datetime.now().isoformat()

    # Ensure directory exists
    status_path.parent.mkdir(parents=True, exist_ok=True)

    # Write atomically (write to temp, then rename)
    temp_path = status_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, indent=2))
    temp_path.rename(status_path)


def read_status(status_path: Path) -> dict[str, Any] | None:
    """Read an agent's status file.

    Args:
        status_path: Path to the status file.

    Returns:
        Status data as a dictionary, or None if file doesn't exist or is invalid.
    """
    if not status_path.exists():
        return None

    try:
        return json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def is_agent_stale(
    status_path: Path, threshold_seconds: int = STALE_THRESHOLD_SECONDS
) -> bool:
    """Check if an agent hasn't updated its status recently.

    Args:
        status_path: Path to the status file.
        threshold_seconds: Seconds without update to consider stale (default: 300).

    Returns:
        True if agent is stale or status file is missing/invalid.
    """
    data = read_status(status_path)
    if not data:
        return True

    try:
        last_update = datetime.fromisoformat(data["last_update"])
        age = (datetime.now() - last_update).total_seconds()
        return age > threshold_seconds
    except (KeyError, ValueError):
        return True


def is_agent_exited(status_path: Path) -> bool:
    """Check if an agent has exited (successfully or with error).

    Args:
        status_path: Path to the status file.

    Returns:
        True if agent status starts with "exited:".
    """
    data = read_status(status_path)
    if not data:
        return False

    status = data.get("status", "")
    return status.startswith("exited:")


def get_agent_pid(status_path: Path) -> int | None:
    """Get the PID of an agent from its status file.

    Args:
        status_path: Path to the status file.

    Returns:
        PID as integer, or None if not available.
    """
    data = read_status(status_path)
    if not data:
        return None

    pid = data.get("pid")
    return int(pid) if pid is not None else None


def list_agent_statuses(prefix: str) -> list[tuple[Path, dict[str, Any]]]:
    """List all agent status files for a given prefix.

    Args:
        prefix: Namespace for organizing status files (e.g., project name).

    Returns:
        List of (path, data) tuples for each status file.
    """
    prefix_dir = STATUS_DIR / prefix
    if not prefix_dir.exists():
        return []

    results = []
    for status_file in prefix_dir.glob("*.status"):
        data = read_status(status_file)
        if data:
            results.append((status_file, data))

    return results
