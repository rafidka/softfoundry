"""Session management utilities for agent persistence.

Sessions are stored centrally at ~/.softfoundry/sessions/ to persist
across different project directories and allow easy backup/cleanup.
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

# Centralized sessions directory
SESSIONS_DIR = Path.home() / ".softfoundry" / "sessions"


@dataclass
class SessionInfo:
    """Information about a saved agent session."""

    session_id: str
    agent_name: str
    agent_type: str  # "manager", "programmer", or "reviewer"
    prefix: str  # Namespace for organizing sessions (e.g., project name)
    last_run: str  # ISO timestamp
    num_turns: int
    total_cost_usd: float | None = None


class SessionManager:
    """Manages session persistence for agents.

    Sessions are stored centrally at ~/.softfoundry/sessions/.
    Each agent has its own session file named `{agent_type}-{sanitized_name}-{prefix}.json`.
    """

    def __init__(self, prefix: str) -> None:
        """Initialize the session manager.

        Args:
            prefix: Namespace for organizing sessions (e.g., project name).
        """
        self.prefix = prefix
        self.sessions_path = SESSIONS_DIR

    def _sanitize_name(self, name: str) -> str:
        """Convert an agent name to a safe filename.

        Args:
            name: The agent name (e.g., "John Doe").

        Returns:
            A sanitized lowercase string (e.g., "john-doe").
        """
        # Convert to lowercase, replace spaces and special chars with hyphens
        sanitized = re.sub(r"[^a-z0-9]+", "-", name.lower())
        # Remove leading/trailing hyphens
        return sanitized.strip("-")

    def _get_session_path(self, agent_type: str, agent_name: str) -> Path:
        """Get the path to a session file.

        Args:
            agent_type: The type of agent ("manager", "programmer", or "reviewer").
            agent_name: The name of the agent.

        Returns:
            Path to the session file.
        """
        sanitized_name = self._sanitize_name(agent_name)
        filename = f"{agent_type}-{sanitized_name}-{self.prefix}.json"
        return self.sessions_path / filename

    def get_session(self, agent_type: str, agent_name: str) -> SessionInfo | None:
        """Retrieve a saved session if it exists.

        Args:
            agent_type: The type of agent ("manager" or "programmer").
            agent_name: The name of the agent.

        Returns:
            SessionInfo if a session exists, None otherwise.
        """
        session_path = self._get_session_path(agent_type, agent_name)

        if not session_path.exists():
            return None

        try:
            with open(session_path) as f:
                data = json.load(f)
            return SessionInfo(**data)
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            # Corrupted session file - log warning and return None
            print(f"Warning: Corrupted session file at {session_path}: {e}")
            return None

    def save_session(self, session_info: SessionInfo) -> None:
        """Save a session to disk.

        Args:
            session_info: The session information to save.
        """
        agent_type = session_info.agent_type
        agent_name = session_info.agent_name
        session_path = self._get_session_path(agent_type, agent_name)

        # Ensure the sessions directory exists
        self.sessions_path.mkdir(parents=True, exist_ok=True)

        with open(session_path, "w") as f:
            json.dump(asdict(session_info), f, indent=2)

    def delete_session(self, agent_type: str, agent_name: str) -> bool:
        """Delete a saved session.

        Args:
            agent_type: The type of agent ("manager" or "programmer").
            agent_name: The name of the agent.

        Returns:
            True if a session was deleted, False if no session existed.
        """
        session_path = self._get_session_path(agent_type, agent_name)

        if session_path.exists():
            session_path.unlink()
            return True
        return False

    def create_session_info(
        self,
        session_id: str,
        agent_name: str,
        agent_type: str,
        num_turns: int,
        total_cost_usd: float | None = None,
    ) -> SessionInfo:
        """Create a new SessionInfo object with current timestamp.

        Args:
            session_id: The session ID from the ResultMessage.
            agent_name: The name of the agent.
            agent_type: The type of agent ("manager", "programmer", or "reviewer").
            num_turns: Number of turns completed.
            total_cost_usd: Total cost in USD (optional).

        Returns:
            A new SessionInfo object.
        """
        return SessionInfo(
            session_id=session_id,
            agent_name=agent_name,
            agent_type=agent_type,
            prefix=self.prefix,
            last_run=datetime.now().isoformat(),
            num_turns=num_turns,
            total_cost_usd=total_cost_usd,
        )


def format_session_info(session: SessionInfo) -> str:
    """Format session info for display to user.

    Args:
        session: The session information to format.

    Returns:
        A human-readable string describing the session.
    """
    # Parse and format the timestamp
    try:
        dt = datetime.fromisoformat(session.last_run)
        timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        timestamp = session.last_run

    lines = [
        f"  Last run: {timestamp}",
        f"  Turns: {session.num_turns}",
    ]

    if session.total_cost_usd is not None:
        lines.append(f"  Cost: ${session.total_cost_usd:.4f}")

    return "\n".join(lines)
