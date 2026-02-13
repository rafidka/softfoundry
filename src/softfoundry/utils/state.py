"""State checking utilities for agent coordination.

This module provides functions to parse markdown files and determine
the current state of programmers, tasks, and the overall project.
"""

import re
from enum import Enum
from pathlib import Path


class ProgrammerState(Enum):
    """Possible states for a programmer agent."""

    NOT_REGISTERED = "not_registered"  # No team file exists
    AVAILABLE = "available"  # Waiting for task assignment
    ASSIGNED = "assigned"  # Has task assigned, should start working
    WORKING = "working"  # Actively working on task


class TaskState(Enum):
    """Possible states for a task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ManagerState(Enum):
    """Possible states for the manager agent."""

    INITIAL = "initial"  # No tasks created yet
    ASSIGNING = "assigning"  # Has pending tasks and/or available programmers
    MONITORING = "monitoring"  # All tasks assigned, monitoring progress
    COMPLETED = "completed"  # All tasks completed


def sanitize_filename(name: str) -> str:
    """Convert a name to a safe filename.

    Args:
        name: The name to sanitize (e.g., "John Doe").

    Returns:
        A sanitized lowercase string (e.g., "john-doe").
    """
    sanitized = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return sanitized.strip("-")


def parse_status_field(content: str, field: str) -> str | None:
    """Parse a markdown file for a status field value.

    Looks for patterns like:
        ## Status
        AVAILABLE

    or:
        ## Assigned Task
        task-001.md

    Args:
        content: The markdown file content.
        field: The field name to look for (e.g., "Status", "Assigned Task").

    Returns:
        The value following the field header, or None if not found.
    """
    # Pattern: ## Field Name followed by a value on the next non-empty line
    pattern = rf"##\s*{re.escape(field)}\s*\n+(?:<!--.*?-->\s*\n+)?([^\n#]+)"
    match = re.search(pattern, content, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        # Ignore if it's a template placeholder
        if value.startswith("{") and value.endswith("}"):
            return None
        return value
    return None


def get_team_file_path(planning_dir: str, agent_name: str) -> Path:
    """Get the path to a programmer's team file.

    Args:
        planning_dir: Path to the planning directory.
        agent_name: Name of the programmer.

    Returns:
        Path to the team file.
    """
    filename = f"{sanitize_filename(agent_name)}.md"
    return Path(planning_dir) / "team" / filename


def get_programmer_state(planning_dir: str, agent_name: str) -> ProgrammerState:
    """Check the programmer's team file to determine their state.

    Args:
        planning_dir: Path to the planning directory.
        agent_name: Name of the programmer.

    Returns:
        The programmer's current state.
    """
    team_file = get_team_file_path(planning_dir, agent_name)

    if not team_file.exists():
        return ProgrammerState.NOT_REGISTERED

    try:
        content = team_file.read_text()
    except OSError:
        return ProgrammerState.NOT_REGISTERED

    status = parse_status_field(content, "Status")
    if status is None:
        return ProgrammerState.NOT_REGISTERED

    status_lower = status.lower()
    if "working" in status_lower:
        return ProgrammerState.WORKING
    elif "assigned" in status_lower:
        return ProgrammerState.ASSIGNED
    elif "available" in status_lower:
        return ProgrammerState.AVAILABLE
    else:
        # Unknown status, treat as available
        return ProgrammerState.AVAILABLE


def get_assigned_task(planning_dir: str, agent_name: str) -> str | None:
    """Get the filename of the task assigned to the programmer.

    Args:
        planning_dir: Path to the planning directory.
        agent_name: Name of the programmer.

    Returns:
        The task filename if assigned, None otherwise.
    """
    team_file = get_team_file_path(planning_dir, agent_name)

    if not team_file.exists():
        return None

    try:
        content = team_file.read_text()
    except OSError:
        return None

    task = parse_status_field(content, "Assigned Task")
    if task and task.lower() not in ("none", "unassigned", "n/a", "-"):
        return task
    return None


def get_task_state(planning_dir: str, task_filename: str) -> TaskState:
    """Check a task file for its status.

    Args:
        planning_dir: Path to the planning directory.
        task_filename: Filename of the task.

    Returns:
        The task's current state.
    """
    task_file = Path(planning_dir) / "tasks" / task_filename

    if not task_file.exists():
        return TaskState.PENDING

    try:
        content = task_file.read_text()
    except OSError:
        return TaskState.PENDING

    status = parse_status_field(content, "Status")
    if status is None:
        return TaskState.PENDING

    status_lower = status.lower()
    if "completed" in status_lower:
        return TaskState.COMPLETED
    elif "in_progress" in status_lower or "in progress" in status_lower:
        return TaskState.IN_PROGRESS
    else:
        return TaskState.PENDING


def get_all_tasks(planning_dir: str) -> list[tuple[str, TaskState]]:
    """Get all task files and their states.

    Args:
        planning_dir: Path to the planning directory.

    Returns:
        List of (filename, state) tuples.
    """
    tasks_dir = Path(planning_dir) / "tasks"
    if not tasks_dir.exists():
        return []

    tasks = []
    for task_file in tasks_dir.glob("*.md"):
        # Skip template file
        if task_file.name.lower() == "template.md":
            continue
        state = get_task_state(planning_dir, task_file.name)
        tasks.append((task_file.name, state))

    return tasks


def get_all_team_members(planning_dir: str) -> list[tuple[str, ProgrammerState]]:
    """Get all team members and their states.

    Args:
        planning_dir: Path to the planning directory.

    Returns:
        List of (filename, state) tuples.
    """
    team_dir = Path(planning_dir) / "team"
    if not team_dir.exists():
        return []

    members = []
    for member_file in team_dir.glob("*.md"):
        # Skip template file
        if member_file.name.lower() == "template.md":
            continue

        try:
            content = member_file.read_text()
        except OSError:
            continue

        status = parse_status_field(content, "Status")
        if status is None:
            state = ProgrammerState.NOT_REGISTERED
        elif "working" in status.lower():
            state = ProgrammerState.WORKING
        elif "assigned" in status.lower():
            state = ProgrammerState.ASSIGNED
        elif "available" in status.lower():
            state = ProgrammerState.AVAILABLE
        else:
            state = ProgrammerState.AVAILABLE

        members.append((member_file.name, state))

    return members


def get_available_programmers(planning_dir: str) -> list[str]:
    """Get filenames of programmers with AVAILABLE status.

    Args:
        planning_dir: Path to the planning directory.

    Returns:
        List of team file filenames for available programmers.
    """
    members = get_all_team_members(planning_dir)
    return [name for name, state in members if state == ProgrammerState.AVAILABLE]


def get_pending_tasks(planning_dir: str) -> list[str]:
    """Get filenames of tasks with PENDING status.

    Args:
        planning_dir: Path to the planning directory.

    Returns:
        List of task filenames.
    """
    tasks = get_all_tasks(planning_dir)
    return [name for name, state in tasks if state == TaskState.PENDING]


def get_manager_state(planning_dir: str) -> ManagerState:
    """Analyze all tasks and team members to determine manager state.

    Args:
        planning_dir: Path to the planning directory.

    Returns:
        The manager's current state based on project status.
    """
    tasks = get_all_tasks(planning_dir)

    # No tasks created yet
    if not tasks:
        return ManagerState.INITIAL

    # Check if all tasks are completed
    all_completed = all(state == TaskState.COMPLETED for _, state in tasks)
    if all_completed:
        return ManagerState.COMPLETED

    # Check if there are pending tasks or available programmers
    pending_tasks = [name for name, state in tasks if state == TaskState.PENDING]
    available_programmers = get_available_programmers(planning_dir)

    if pending_tasks and available_programmers:
        # Has work to assign
        return ManagerState.ASSIGNING
    elif pending_tasks:
        # Has pending tasks but no available programmers
        return ManagerState.MONITORING
    else:
        # All tasks are in progress or completed (not pending)
        return ManagerState.MONITORING
