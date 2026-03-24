"""Session transcript logging for agent sessions.

Writes Markdown-formatted transcripts to ~/.softfoundry/logs/{namespace}/
so users can review agent sessions after exiting the TUI.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from softfoundry.utils.status import sanitize_name

# Centralized logs directory
LOGS_DIR = Path.home() / ".softfoundry" / "logs"

# Truncate long tool results to prevent huge log files
TOOL_RESULT_MAX_LENGTH = 2000


class TranscriptLogger:
    """Appends Markdown-formatted session transcript entries to a log file.

    Each agent session gets its own .md file under ~/.softfoundry/logs/{namespace}/.
    All methods append to the file — no buffering needed since turn processing
    is sequential.
    """

    def __init__(self, namespace: str, agent_type: str, agent_name: str) -> None:
        """Initialize the transcript logger and write the file header.

        Args:
            namespace: Project namespace (used as subdirectory).
            agent_type: Agent type (e.g., "manager", "programmer").
            agent_name: Agent instance name (e.g., "Alice Chen").
        """
        self._namespace = namespace
        self._agent_type = agent_type
        self._agent_name = agent_name

        # Build log file path
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        sanitized = sanitize_name(agent_name)
        filename = f"{agent_type}-{sanitized}-{timestamp}.md"

        log_dir = LOGS_DIR / namespace
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / filename

        # Write header
        iso_time = now.isoformat(timespec="seconds")
        self._write(
            f"# Session Transcript\n\n"
            f"- **Agent**: {agent_type} / {agent_name}\n"
            f"- **Project**: {namespace}\n"
            f"- **Started**: {iso_time}\n\n"
            f"---\n\n"
        )

    @property
    def log_path(self) -> Path:
        """Path to the transcript log file."""
        return self._log_path

    def _write(self, text: str) -> None:
        """Append text to the log file.

        Args:
            text: Markdown text to append.
        """
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(text)

    def log_assistant_text(self, text: str) -> None:
        """Log an assistant text message.

        Args:
            text: The assistant's text content.
        """
        self._write(f"## Assistant\n\n{text}\n\n")

    def log_thinking(self, text: str) -> None:
        """Log a thinking block (collapsible in Markdown).

        Args:
            text: The thinking content.
        """
        self._write(
            f"<details><summary>Thinking</summary>\n\n{text}\n\n</details>\n\n"
        )

    def log_tool_use(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Log a tool use invocation.

        Args:
            tool_name: Name of the tool being called.
            tool_input: Tool input parameters.
        """
        input_str = json.dumps(tool_input, indent=2, default=str)
        self._write(f"### Tool: {tool_name}\n\n```json\n{input_str}\n```\n\n")

    def log_tool_result(self, content: str, is_error: bool) -> None:
        """Log a tool result.

        Args:
            content: The tool's output content.
            is_error: Whether the result is an error.
        """
        error_suffix = " (ERROR)" if is_error else ""
        truncated = content[:TOOL_RESULT_MAX_LENGTH]
        if len(content) > TOOL_RESULT_MAX_LENGTH:
            truncated += f"\n\n... (truncated, {len(content)} chars total)"
        self._write(f"**Result{error_suffix}**:\n\n```\n{truncated}\n```\n\n")

    def log_user_message(self, text: str) -> None:
        """Log a user message.

        Args:
            text: The user's message text.
        """
        self._write(f"## User\n\n{text}\n\n")

    def log_system_message(self, subtype: str) -> None:
        """Log a system message.

        Args:
            subtype: The system message subtype.
        """
        self._write(f"> System: {subtype}\n\n")

    def log_result(
        self,
        subtype: str,
        duration_ms: int | None = None,
        cost_usd: float | None = None,
        num_turns: int | None = None,
    ) -> None:
        """Log a turn completion result.

        Args:
            subtype: Result subtype (e.g., "end_turn").
            duration_ms: Turn duration in milliseconds.
            cost_usd: Total cost in USD.
            num_turns: Total number of turns.
        """
        parts = [f"Turn complete ({subtype})"]
        if duration_ms is not None:
            parts.append(f"{duration_ms / 1000:.1f}s")
        if cost_usd is not None:
            parts.append(f"${cost_usd:.4f}")
        if num_turns is not None:
            parts.append(f"{num_turns} turns")
        self._write(f"---\n\n*{' — '.join(parts)}*\n\n")

    def log_lifecycle(self, text: str, level: str = "info") -> None:
        """Log a lifecycle event.

        Args:
            text: The lifecycle message.
            level: Severity level (info, warning, error, success).
        """
        self._write(f"> [{level}] {text}\n\n")

    def log_session_end(self, reason: str) -> None:
        """Log the end of the session.

        Args:
            reason: Why the session ended (e.g., "completed", "error: ...").
        """
        iso_time = datetime.now().isoformat(timespec="seconds")
        self._write(f"---\n\n**Session ended**: {reason} at {iso_time}\n")
