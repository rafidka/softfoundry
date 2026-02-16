"""Agent loop framework for building interactive agentic applications.

This module provides the `Agent` abstract base class that handles:
- Signal handling (Ctrl+C graceful/immediate shutdown)
- Session management (resume, new, interactive prompt)
- Status file lifecycle (starting, exited:*)
- The main agent loop with user interaction detection

Subclasses implement agent-specific logic via abstract methods.
"""

import asyncio
import signal
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from softfoundry.utils.input import read_multiline_input
from softfoundry.utils.llm import needs_user_input
from softfoundry.utils.output import MessagePrinter, create_printer
from softfoundry.utils.sessions import SessionManager, format_session_info
from softfoundry.utils.status import get_status_path, read_status, update_status


class GracefulExit(Exception):
    """Raised when user requests graceful shutdown (first Ctrl+C)."""

    pass


class ImmediateExit(Exception):
    """Raised when user requests immediate shutdown (second Ctrl+C)."""

    pass


def extract_assistant_text(message: AssistantMessage) -> str:
    """Extract text content from an AssistantMessage.

    Args:
        message: The AssistantMessage to extract text from.

    Returns:
        Concatenated text from all TextBlocks in the message.
    """
    texts = []
    for block in message.content:
        if isinstance(block, TextBlock):
            texts.append(block.text)
    return "\n".join(texts)


@dataclass
class AgentConfig:
    """Configuration for an agent.

    Attributes:
        project: Namespace for sessions, logs, and status files.
            This is NOT GitHub-specific; it's used to organize agent data.
        agent_type: Category of agent (e.g., "manager", "programmer", "reviewer").
        agent_name: Instance name (e.g., "Alice Chen", "default").
        allowed_tools: List of tools the agent can use.
        permission_mode: Permission mode for Claude SDK.
        cwd: Working directory for the agent.
        max_iterations: Maximum loop iterations (safety limit).
        resume: If True, automatically resume existing session.
        new_session: If True, force a new session (delete existing).
        verbosity: Output verbosity level ("minimal", "medium", "verbose").
    """

    # Identity & Namespacing
    project: str
    agent_type: str
    agent_name: str = "default"

    # Claude SDK options
    allowed_tools: list[str] = field(
        default_factory=lambda: ["Read", "Edit", "Glob", "Write", "Bash", "Grep"]
    )
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = (
        "acceptEdits"
    )
    cwd: str | Path | None = None

    # Loop behavior
    max_iterations: int = 100

    # Session handling
    resume: bool = False
    new_session: bool = False

    # Output
    verbosity: str = "medium"


class Agent(ABC):
    """Abstract base class for building interactive agentic loops.

    This class handles the common infrastructure for running an agent:
    - Signal handling (Ctrl+C graceful/immediate shutdown)
    - Session management (resume, new, interactive prompt)
    - Status file lifecycle (starting, exited:*)
    - The main agent loop with user interaction detection

    Subclasses implement the agent-specific logic via abstract methods:
    - `get_system_prompt()`: Define the agent's personality and instructions
    - `get_initial_prompt()`: The first message to start the agent
    - `is_complete()`: Check if the agent's work is done
    - `get_continuation_prompt()`: What to say to keep the agent working

    Example:
        ```python
        class MyAgent(Agent):
            def get_system_prompt(self) -> str:
                return "You are a helpful assistant."

            def get_initial_prompt(self) -> str:
                return "Start working on the task."

            def is_complete(self, result: ResultMessage) -> bool:
                return "DONE" in (result.result or "")

            def get_continuation_prompt(self) -> str:
                return "Continue working."

        agent = MyAgent(AgentConfig(project="myproject", agent_type="worker"))
        await agent.run()
        ```
    """

    def __init__(self, config: AgentConfig):
        """Initialize the agent with the given configuration.

        Args:
            config: The agent configuration.
        """
        self.config = config
        self._printer = create_printer(config.verbosity)

        # Session management
        self._session_manager = SessionManager(prefix=config.project)
        self._session_id: str | None = None
        self._resolve_session()

        # Status management
        self._status_path = get_status_path(
            prefix=config.project,
            agent_type=config.agent_type,
            agent_name=config.agent_name,
        )
        self.update_status("starting", "Initializing agent")

        # Internal state
        self._iteration = 0
        self._shutdown_state: dict[str, bool] = {}
        self._last_assistant_text = ""

    # ─────────────────────────────────────────────────────────────────────────
    # SESSION MANAGEMENT (handled by parent)
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_session(self) -> None:
        """Handle session resume/new logic based on config flags.

        This method is called during __init__ to determine if we should
        resume an existing session or start a new one.

        Raises:
            ValueError: If resume=True but no existing session found.
        """
        existing = self._session_manager.get_session(
            self.config.agent_type,
            self.config.agent_name,
        )

        if existing:
            if self.config.new_session:
                self._session_manager.delete_session(
                    self.config.agent_type,
                    self.config.agent_name,
                )
                self._printer.console.print("Deleted existing session.")
            elif self.config.resume:
                self._session_id = existing.session_id
                self._printer.console.print("Resuming previous session...")
            else:
                # Interactive prompt
                self._printer.console.print("Found previous session:")
                print(format_session_info(existing))
                response = input("Continue previous session? [y/N]: ").strip().lower()
                if response == "y":
                    self._session_id = existing.session_id
                    self._printer.console.print("Resuming session...")
                else:
                    self._session_manager.delete_session(
                        self.config.agent_type,
                        self.config.agent_name,
                    )
                    self._printer.console.print("Starting new session...")
        elif self.config.resume:
            raise ValueError(
                f"No existing session found for {self.config.agent_name}. "
                "Run without --resume to start a new session."
            )

    def _save_session(
        self, session_id: str, num_turns: int, cost_usd: float | None
    ) -> None:
        """Save session info for crash recovery.

        Called automatically by the loop after each ResultMessage.

        Args:
            session_id: The session ID from the ResultMessage.
            num_turns: Number of turns completed.
            cost_usd: Total cost in USD (may be None).
        """
        session_info = self._session_manager.create_session_info(
            session_id=session_id,
            agent_name=self.config.agent_name,
            agent_type=self.config.agent_type,
            num_turns=num_turns,
            total_cost_usd=cost_usd,
        )
        self._session_manager.save_session(session_info)

    # ─────────────────────────────────────────────────────────────────────────
    # STATUS MANAGEMENT (helpers for subclasses)
    # ─────────────────────────────────────────────────────────────────────────

    def update_status(self, status: str, details: str = "", **extra: Any) -> None:
        """Update the agent's status file.

        Args:
            status: Current status (e.g., "working", "idle", "exited:success").
            details: Human-readable description of current activity.
            **extra: Additional fields to include (e.g., current_issue=3).
        """
        update_status(
            self._status_path,
            status=status,
            details=details,
            agent_type=self.config.agent_type,
            name=self.config.agent_name,
            project=self.config.project,
            **extra,
        )

    def read_status(self) -> dict[str, Any] | None:
        """Read the current status file.

        Returns:
            Status data as a dictionary, or None if file doesn't exist.
        """
        return read_status(self._status_path)

    # ─────────────────────────────────────────────────────────────────────────
    # SIGNAL HANDLING
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        self._shutdown_state = {"shutdown_requested": False, "query_running": False}

        def handler(signum: int, frame: object) -> None:
            if self._shutdown_state["shutdown_requested"]:
                print("\nImmediate shutdown requested.")
                raise ImmediateExit()
            else:
                self._shutdown_state["shutdown_requested"] = True
                if self._shutdown_state["query_running"]:
                    print(
                        "\nShutdown requested. Waiting for current query to complete..."
                    )
                    print("Press Ctrl+C again to exit immediately.")
                else:
                    raise GracefulExit()

        signal.signal(signal.SIGINT, handler)

    # ─────────────────────────────────────────────────────────────────────────
    # ABSTRACT METHODS - Must implement
    # ─────────────────────────────────────────────────────────────────────────

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt defining the agent's behavior and personality.

        This prompt sets up the agent's capabilities, personality, workflow,
        and any domain-specific instructions.

        Returns:
            The system prompt string.
        """
        ...

    @abstractmethod
    def get_initial_prompt(self) -> str:
        """Return the first prompt to send to start the agent's work.

        This should include any context needed to begin (e.g., crash recovery
        information, initial task description).

        Returns:
            The initial prompt string.
        """
        ...

    @abstractmethod
    def is_complete(self, result: ResultMessage) -> bool:
        """Check if the agent's work is done.

        Called after each ResultMessage. Return True to exit the loop
        successfully.

        Args:
            result: The ResultMessage from the completed turn.

        Returns:
            True if the agent should exit (work complete), False to continue.
        """
        ...

    @abstractmethod
    def get_continuation_prompt(self) -> str:
        """Return the prompt to send when the agent should keep working.

        Called when the agent finished a turn but didn't ask a question
        that requires user input.

        Returns:
            The continuation prompt string.
        """
        ...

    # ─────────────────────────────────────────────────────────────────────────
    # OPTIONAL OVERRIDES - Have sensible defaults
    # ─────────────────────────────────────────────────────────────────────────

    def get_idle_interval(self) -> int | None:
        """Return seconds to wait before sending continuation prompt.

        Called before sending the continuation prompt. Return None to
        continue immediately, or a number of seconds to wait (useful
        for polling scenarios).

        Returns:
            Seconds to wait, or None to continue immediately.
        """
        return None

    def needs_user_input(self, text: str) -> bool:
        """Determine if the agent's response requires user input.

        Default implementation uses an LLM to classify whether the text
        contains a question that needs user input.

        Override this method to customize user input detection.

        Args:
            text: The agent's last response text.

        Returns:
            True if user input is needed, False otherwise.
        """
        return needs_user_input(text)

    def on_assistant_message(self, message: AssistantMessage, text: str) -> None:
        """Hook called when the assistant sends a message.

        Override to perform custom processing on assistant messages
        (e.g., extracting state, logging).

        Args:
            message: The full AssistantMessage object.
            text: Extracted text content from the message.
        """
        pass

    def on_result(self, result: ResultMessage) -> None:
        """Hook called when a result is received (before is_complete check).

        Override to perform custom processing on results (e.g., updating
        status with task-specific information).

        Args:
            result: The ResultMessage from the completed turn.
        """
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # LIFECYCLE HOOKS - Default implementations, override if needed
    # ─────────────────────────────────────────────────────────────────────────

    def on_shutdown(self, graceful: bool) -> None:
        """Called when the agent is shutting down via Ctrl+C.

        Default implementation updates the status file and prints a message.

        Args:
            graceful: True if this is a graceful shutdown (first Ctrl+C),
                False if immediate (second Ctrl+C).
        """
        self.update_status(
            "exited:terminated",
            "User requested shutdown" if graceful else "Immediate exit",
        )
        if graceful:
            self._printer.console.print("[yellow]Exiting...[/yellow]")
        else:
            self._printer.console.print("[red]Immediate exit.[/red]")

    def on_error(self, error: Exception) -> None:
        """Called when an unhandled exception occurs.

        Default implementation updates the status file with the error.

        Args:
            error: The exception that occurred.
        """
        self.update_status("exited:error", f"Error: {error}")

    def on_complete(self) -> None:
        """Called when is_complete() returns True.

        Default implementation updates the status file and prints a message.
        """
        self.update_status("exited:success", "Completed successfully")
        self._printer.console.print("[bold green]Agent completed![/bold green]")

    def on_max_iterations(self) -> None:
        """Called when max_iterations is reached.

        Default implementation updates the status file and prints a warning.
        """
        self.update_status(
            "exited:terminated",
            f"Reached max iterations: {self.config.max_iterations}",
        )
        self._printer.console.print(
            f"[yellow]Reached maximum iterations ({self.config.max_iterations}). "
            f"Exiting.[/yellow]"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # CONCRETE METHODS - Provided by framework
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def printer(self) -> MessagePrinter:
        """Access the message printer for custom output.

        Returns:
            The MessagePrinter instance configured for this agent.
        """
        return self._printer

    def _get_cwd(self) -> str | None:
        """Get the resolved working directory.

        Returns:
            The resolved cwd path string, or None if not set or doesn't exist.
        """
        if self.config.cwd:
            cwd_path = Path(self.config.cwd)
            if cwd_path.exists():
                return str(cwd_path.resolve())
        return None

    async def run(self) -> None:
        """Main entry point - runs the agent loop.

        This method:
        1. Sets up signal handlers
        2. Creates the Claude SDK client
        3. Sends the initial prompt
        4. Loops until completion, shutdown, or max iterations

        Raises:
            Exception: Re-raises any exception after calling on_error().
        """
        self._setup_signal_handlers()

        # Build Claude SDK options
        options = ClaudeAgentOptions(
            allowed_tools=self.config.allowed_tools,
            permission_mode=self.config.permission_mode,
            resume=self._session_id,
            system_prompt=self.get_system_prompt(),
            cwd=self._get_cwd(),
        )

        try:
            async with ClaudeSDKClient(options=options) as client:
                # Send initial prompt
                await client.query(self.get_initial_prompt())

                while self._iteration < self.config.max_iterations:
                    self._iteration += 1

                    # Check for shutdown before processing
                    if self._shutdown_state.get("shutdown_requested"):
                        self.on_shutdown(graceful=True)
                        break

                    # Receive and process messages
                    self._last_assistant_text = ""
                    self._shutdown_state["query_running"] = True
                    should_exit = False

                    try:
                        async for message in client.receive_response():
                            self._printer.print_message(message)

                            if isinstance(message, AssistantMessage):
                                text = extract_assistant_text(message)
                                self._last_assistant_text = text
                                self.on_assistant_message(message, text)

                            if isinstance(message, ResultMessage):
                                # Save session for crash recovery
                                self._save_session(
                                    session_id=message.session_id,
                                    num_turns=message.num_turns,
                                    cost_usd=message.total_cost_usd,
                                )

                                # Call hook
                                self.on_result(message)

                                # Check completion
                                if self.is_complete(message):
                                    self.on_complete()
                                    should_exit = True
                    finally:
                        self._shutdown_state["query_running"] = False

                    if should_exit:
                        return

                    # Check for shutdown after processing
                    if self._shutdown_state.get("shutdown_requested"):
                        self.on_shutdown(graceful=True)
                        break

                    # Determine next action
                    if self.needs_user_input(self._last_assistant_text):
                        # Wait for user input (required)
                        self._printer.console.print(
                            "[cyan]Waiting for your input...[/cyan]"
                        )
                        while True:
                            user_input = read_multiline_input().strip()
                            if user_input:
                                await client.query(user_input)
                                break
                            else:
                                self._printer.console.print(
                                    "[yellow]Input required. Please provide a response.[/yellow]"
                                )
                    else:
                        # Check if we should wait before continuing
                        idle_interval = self.get_idle_interval()
                        if idle_interval is not None and idle_interval > 0:
                            self._printer.console.print(
                                f"[dim]Waiting {idle_interval}s before continuing...[/dim]"
                            )
                            try:
                                await asyncio.sleep(idle_interval)
                            except asyncio.CancelledError:
                                break

                        # Send continuation prompt
                        await client.query(self.get_continuation_prompt())

                # Check if we hit max iterations
                if self._iteration >= self.config.max_iterations:
                    self.on_max_iterations()

        except GracefulExit:
            self.on_shutdown(graceful=True)
        except ImmediateExit:
            self.on_shutdown(graceful=False)
            sys.exit(1)
        except Exception as e:
            self.on_error(e)
            raise
