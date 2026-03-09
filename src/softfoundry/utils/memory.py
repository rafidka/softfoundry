"""Agent memory file management for persistent context across sessions.

Memory files are stored alongside status files at
~/.softfoundry/agents/{prefix}/ and contain markdown notes that the agent
writes to capture learnings, patterns, and context from previous sessions.

The memory file contents are injected into the system prompt when
memory is enabled, giving the agent access to its accumulated knowledge.
"""

from pathlib import Path

from softfoundry.utils.status import STATUS_DIR, sanitize_name

# Default maximum lines to inject into system prompt
DEFAULT_MAX_LINES = 200


def get_memory_path(prefix: str, agent_type: str, agent_name: str) -> Path:
    """Get path to an agent's memory file.

    Uses the same naming convention as status files, but with a
    .memory.md extension.

    Args:
        prefix: Namespace for organizing files (e.g., project name).
        agent_type: Type of agent ("manager", "programmer", "reviewer").
        agent_name: Agent name (e.g., "Alice Chen").

    Returns:
        Path to the memory file (e.g.,
        ~/.softfoundry/agents/myproject/programmer-alice-chen.memory.md).
    """
    dir_path = STATUS_DIR / prefix
    dir_path.mkdir(parents=True, exist_ok=True)

    if agent_name and agent_name != agent_type and agent_name != "default":
        filename = f"{agent_type}-{sanitize_name(agent_name)}.memory.md"
    else:
        filename = f"{agent_type}.memory.md"

    return dir_path / filename


def read_memory(memory_path: Path, max_lines: int = DEFAULT_MAX_LINES) -> str:
    """Read an agent's memory file, truncated to max_lines.

    Args:
        memory_path: Path to the memory file.
        max_lines: Maximum number of lines to return. Lines beyond this
            limit are silently dropped with a truncation notice.

    Returns:
        Memory file contents as a string, or empty string if the file
        doesn't exist or can't be read.
    """
    if not memory_path.exists():
        return ""

    try:
        content = memory_path.read_text()
    except OSError:
        return ""

    lines = content.splitlines(keepends=True)
    if len(lines) <= max_lines:
        return content

    truncated = "".join(lines[:max_lines])
    truncated += (
        f"\n\n<!-- Memory truncated: showing {max_lines} of {len(lines)} lines. "
        "Keep your memory concise to avoid truncation. -->\n"
    )
    return truncated
