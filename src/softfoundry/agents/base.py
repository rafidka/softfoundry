"""Agent loop framework for building interactive agentic applications.

This module provides the `Agent` abstract base class that handles:
- Session management (resume, new, interactive prompt)
- Status file lifecycle (starting, exited:*)
- The main agent loop with TUI-based user interaction

Subclasses implement agent-specific logic via abstract methods.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)
from pydantic import BaseModel, Field

from softfoundry.agents.memory import get_memory_path, read_memory
from softfoundry.agents.sessions import SessionManager, format_session_info
from softfoundry.mcp.user_server import UserInputServer, create_user_server
from softfoundry.tui import AgentBridge, SoftFoundryApp
from softfoundry.utils.env import get_claude_code_token
from softfoundry.utils.output import MessagePrinter, create_printer
from softfoundry.utils.status import get_status_path, read_status, update_status

# Heartbeat interval for status file updates (seconds)
HEARTBEAT_INTERVAL = 60


class TurnResult(BaseModel):
    """Result of processing one agent turn.

    Attributes:
        should_exit: True if is_complete() returned True.
        was_interrupted: True if the turn was interrupted by user input.
    """

    should_exit: bool = False
    was_interrupted: bool = False


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


class AgentConfig(BaseModel):
    """Configuration for an agent.

    Attributes:
        namespace: Namespace for sessions, logs, and status files.
        agent_type: Category of agent (e.g., "manager", "programmer", "reviewer").
        agent_name: Instance name (e.g., "Alice Chen", "default").
        allowed_tools: List of tools the agent can use.
        permission_mode: Permission mode for Claude SDK.
        cwd: Working directory for the agent.
        mcp_servers: MCP server configurations (dict of name -> config).
        max_iterations: Maximum loop iterations (safety limit).
        resume: If True, automatically resume existing session.
        new_session: If True, force a new session (delete existing).
        verbosity: Output verbosity level ("minimal", "medium", "verbose").
    """

    # Identity & Namespacing
    namespace: str
    agent_type: str
    agent_name: str = "default"

    # Claude SDK options
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["Read", "Edit", "Glob", "Write", "Bash", "Grep"]
    )
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = (
        "acceptEdits"
    )
    cwd: str | Path | None = None
    mcp_servers: dict[str, Any] = Field(default_factory=dict)

    # Loop behavior
    max_iterations: int = 100

    # Memory
    memory_enabled: bool = False

    # Session handling
    resume: bool = False
    new_session: bool = False

    # Output
    verbosity: str = "medium"

    # Dry mode (skip mutations like session resolution and status writes)
    dry_mode: bool = False


class Agent(ABC):
    """Abstract base class for building interactive agentic loops.

    This class handles the common infrastructure for running an agent:
    - Session management (resume, new, interactive prompt)
    - Status file lifecycle (starting, exited:*)
    - The main agent loop with TUI-based user interaction
    - User input via ask_user MCP tools (auto-injected)

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

        agent = MyAgent(AgentConfig(namespace="myproject", agent_type="worker"))
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
        self._session_manager = SessionManager(prefix=config.namespace)
        self._session_id: str | None = None
        if not config.dry_mode:
            self._resolve_session()

        # Status management
        self._status_path = get_status_path(
            prefix=config.namespace,
            agent_type=config.agent_type,
            agent_name=config.agent_name,
        )
        if not config.dry_mode:
            self.update_status("starting", "Initializing agent")

        # Memory management
        if config.memory_enabled:
            self._memory_path = get_memory_path(
                prefix=config.namespace,
                agent_type=config.agent_type,
                agent_name=config.agent_name,
            )
        else:
            self._memory_path = None

        # Internal state
        self._iteration = 0
        self._last_assistant_text = ""

        # TUI bridge and SDK client (set up later in run())
        self._bridge: AgentBridge | None = None
        self._app: SoftFoundryApp | None = None
        self._client: ClaudeSDKClient | None = None

        # User input MCP server (auto-injected in run())
        self._user_server: UserInputServer | None = None

        # User input handling (no queue - just a single pending input slot)
        self._pending_input: str | None = None
        self._is_turn_running = False

        # Completion tracking (for TUI dismiss behavior)
        self._completed = False

        # Heartbeat tracking
        self._last_heartbeat: float = time.time()

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
            project=self.config.namespace,
            **extra,
        )

    def read_status(self) -> dict[str, Any] | None:
        """Read the current status file.

        Returns:
            Status data as a dictionary, or None if file doesn't exist.
        """
        return read_status(self._status_path)

    def _maybe_heartbeat(self) -> None:
        """Update status file if enough time has passed since last update.

        This ensures the status file's last_update timestamp is fresh,
        allowing other agents (e.g., the manager) to detect stale agents.
        """
        now = time.time()
        if now - self._last_heartbeat >= HEARTBEAT_INTERVAL:
            current = self.read_status()
            if current:
                # Preserve current status, just refresh the timestamp
                self.update_status(
                    current.get("status", "working"),
                    current.get("details", ""),
                    current_issue=current.get("current_issue"),
                    current_pr=current.get("current_pr"),
                )
            self._last_heartbeat = now

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

    def on_shutdown(self) -> None:
        """Called when the agent is shutting down via Ctrl+C.

        Default implementation updates the status file.
        """
        self.update_status("exited:terminated", "User interrupted")

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
    def printer(self) -> MessagePrinter | AgentBridge:
        """Access the message printer for custom output.

        Returns either the original MessagePrinter or the AgentBridge
        (which provides the same print_message interface).

        Returns:
            The printer instance configured for this agent.
        """
        return self._printer

    @property
    def memory_path(self) -> Path | None:
        """Access the memory file path (None if memory is disabled).

        Returns:
            Path to the memory file, or None.
        """
        return self._memory_path

    def _build_system_prompt(self) -> str:
        """Build the full system prompt, including user interaction and memory.

        Calls `get_system_prompt()` for the subclass-defined prompt, then
        appends instructions for ask_user tools and memory section if enabled.

        Returns:
            The complete system prompt string.
        """
        prompt = self.get_system_prompt()

        # Add ask_user instructions
        prompt += """

## User Interaction

When you need to ask the user a question, get clarification, or need them to make a decision, \
use the `ask_user` or `ask_user_choice` tool instead of writing the question in your text response. \
These tools will display the question in the TUI and wait for the user's answer, which is returned \
as the tool result. Do NOT ask questions in plain text — the user cannot respond to those."""

        if self._memory_path is None:
            return prompt

        memory_contents = read_memory(self._memory_path)

        memory_section = f"""

## Your Memory

You have a persistent memory file at `{self._memory_path}`.
Its contents persist across sessions. Use the Write and Edit tools to update it.

**Guidelines:**
- Keep it concise and organized by topic (not chronologically)
- Record: patterns, architecture decisions, file paths, debugging insights, project conventions
- Remove or update outdated information
- Do NOT record session-specific context (current task, in-progress work)
"""

        if memory_contents:
            memory_section += f"""
**Current memory contents:**

{memory_contents}"""
        else:
            memory_section += """
**Current memory contents:**

(empty -- this is your first session)"""

        return prompt + memory_section

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

    # ─────────────────────────────────────────────────────────────────────────
    # INPUT HANDLING
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_user_input(self, text: str) -> None:
        """Handle user input from the TUI.

        This is called by the Textual app when the user submits input.
        Routes input to the ask_user tool if it's waiting, otherwise
        treats it as an interrupt or stores it for later processing.

        Args:
            text: The user's input text.
        """
        # If ask_user tool is waiting for a response, route input there
        if self._user_server and self._user_server.is_waiting:
            self._user_server.provide_response(text)
            return

        # Otherwise, standard behavior: store as pending input
        self._pending_input = text

        if self._is_turn_running and self._client:
            # Agent is busy - interrupt and process the input
            if self._bridge:
                self._bridge.show_lifecycle_message(
                    f"Interrupting with: {text[:50]}{'...' if len(text) > 50 else ''}",
                    "info",
                )
            # Schedule interrupt on the event loop
            asyncio.create_task(self._interrupt_current_turn())

    async def _interrupt_current_turn(self) -> None:
        """Interrupt the current turn."""
        if self._client:
            if self._bridge:
                self._bridge.status = "interrupting"
            await self._client.interrupt()

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_user_server(self) -> None:
        """Create the user input MCP server and inject it into config.

        This adds the ask_user / ask_user_choice tools to the agent's
        allowed tools and MCP servers. Safe to call multiple times (idempotent).
        """
        if self._user_server is not None:
            return  # Already set up
        user_server_config, self._user_server = create_user_server(name="user")
        self.config.mcp_servers["user"] = user_server_config
        self.config.allowed_tools.extend(
            [
                "mcp__user__ask_user",
                "mcp__user__ask_user_choice",
            ]
        )

    def _attach_tui(self, app: SoftFoundryApp, bridge: AgentBridge) -> None:
        """Attach this agent to an existing TUI app and bridge.

        Used by persistent TUI mode where the TUI outlives individual
        agent sessions.

        Args:
            app: The Textual app to use.
            bridge: The bridge to use for TUI communication.
        """
        self._app = app
        self._bridge = bridge
        self._bridge._verbosity = self.config.verbosity
        self._printer = self._bridge  # type: ignore[assignment]

        # Reconnect user server to the bridge
        if self._user_server:
            self._user_server.set_bridge(self._bridge)

        # Rewire the app's input callback to this agent
        self._app._on_input = self._handle_user_input

    async def run(self) -> None:
        """Main entry point - runs the agent loop via the Textual TUI.

        This method:
        1. Creates the user input MCP server and injects it into config
        2. Builds the Claude SDK options
        3. Creates the Textual TUI app and bridge
        4. Connects the user server to the bridge
        5. Runs the agent loop as a Textual worker inside the app

        The TUI stays open after completion so the user can review the
        conversation. Press Ctrl+D to exit.

        Raises:
            Exception: Re-raises any exception after calling on_error().
        """
        self._setup_user_server()

        # Build Claude SDK options (includes injected user server)
        options = self._build_sdk_options()

        # Store options for the worker to use
        self._sdk_options = options

        # Create the Textual TUI app
        self._app = SoftFoundryApp(
            agent_type=self.config.agent_type,
            agent_name=self.config.agent_name,
            project=self.config.namespace,
            epic_number=getattr(self, "_epic_number", None),
            on_input=self._handle_user_input,
            agent_coroutine=self._agent_worker,
        )

        # Create the bridge (adapter between agent and TUI)
        self._bridge = AgentBridge(app=self._app)
        self._bridge._verbosity = self.config.verbosity

        # Connect user server to bridge so it can display questions in TUI
        assert self._user_server is not None
        self._user_server.set_bridge(self._bridge)

        # Replace the printer with the bridge (it implements print_message)
        # The bridge's console property provides backward compatibility
        self._printer = self._bridge  # type: ignore[assignment]

        try:
            await self._app.run_async()
        except KeyboardInterrupt:
            if self._bridge:
                self._bridge.show_lifecycle_message(
                    "Interrupted. Exiting...", "warning"
                )
            self.on_shutdown()
        except Exception as e:
            self.on_error(e)
            raise

    async def run_session(
        self,
        app: SoftFoundryApp,
        bridge: AgentBridge,
    ) -> None:
        """Run one SDK session using an existing (persistent) TUI.

        Unlike run(), this does NOT create or destroy the TUI. It:
        1. Sets up the user server (if not already done)
        2. Attaches to the provided TUI app/bridge
        3. Runs the agent loop (_run_loop)
        4. Returns when the session completes (TUI stays open)

        Used by the outer loops in run_programmer/run_reviewer for
        persistent TUI mode.

        Args:
            app: The persistent Textual app.
            bridge: The persistent bridge.

        Raises:
            Exception: Re-raises any exception after calling on_error().
        """
        self._setup_user_server()
        self._attach_tui(app, bridge)

        # Build SDK options
        self._sdk_options = self._build_sdk_options()

        # Reset iteration counter for this session
        self._iteration = 0

        try:
            await self._run_loop(self._sdk_options)
        except KeyboardInterrupt:
            if self._bridge:
                self._bridge.show_lifecycle_message(
                    "Interrupted. Exiting...", "warning"
                )
            self.on_shutdown()
            raise
        except Exception as e:
            self.on_error(e)
            if self._bridge:
                self._bridge.show_lifecycle_message(f"Agent error: {e}", "error")
            raise

    def _build_sdk_options(self) -> ClaudeAgentOptions:
        """Build the Claude SDK options from config.

        Returns:
            ClaudeAgentOptions ready for use with ClaudeSDKClient.
        """
        return ClaudeAgentOptions(
            allowed_tools=self.config.allowed_tools,
            permission_mode=self.config.permission_mode,
            mcp_servers=self.config.mcp_servers if self.config.mcp_servers else {},
            resume=self._session_id,
            system_prompt=self._build_system_prompt(),
            cwd=self._get_cwd(),
            env={
                "ANTHROPIC_API_KEY": "",
                "CLAUDE_CODE_OAUTH_TOKEN": get_claude_code_token(),
            },
        )

    async def _agent_worker(self) -> None:
        """The agent loop coroutine, run as a Textual worker.

        This is the async function passed to SoftFoundryApp.agent_coroutine.
        It runs the full agent loop within the Textual event loop.

        On completion, the TUI stays open so the user can review the
        conversation and press Ctrl+D to exit.
        """
        try:
            await self._run_loop(self._sdk_options)
        except KeyboardInterrupt:
            if self._bridge:
                self._bridge.show_lifecycle_message(
                    "Interrupted. Exiting...", "warning"
                )
            self.on_shutdown()
        except Exception as e:
            self.on_error(e)
            if self._bridge:
                self._bridge.show_lifecycle_message(f"Agent error: {e}", "error")

        # Give time for final messages to render
        await asyncio.sleep(0.5)

        if self._completed and self._bridge:
            # Agent completed — keep TUI open for user to review
            self._bridge.show_lifecycle_message("Press Ctrl+D to exit.", "info")
            self._bridge.enable()
            # Don't call app.exit() — user will press Ctrl+D when ready
        elif self._app:
            # Error or interrupt — auto-exit
            self._app.exit()

    async def _run_loop(self, options: ClaudeAgentOptions) -> None:
        """Run the main agent loop.

        Args:
            options: The ClaudeAgentOptions for the SDK client.
        """
        assert self._bridge is not None

        async with ClaudeSDKClient(options=options) as client:
            self._client = client

            try:
                # Send initial prompt
                await self._send_message(self.get_initial_prompt())

                while self._iteration < self.config.max_iterations:
                    self._iteration += 1

                    result = await self._process_turn()

                    # Update heartbeat after each turn
                    self._maybe_heartbeat()

                    if result.should_exit:
                        return

                    next_prompt = await self._get_next_prompt(result.was_interrupted)
                    if next_prompt:
                        await self._send_message(next_prompt)

                # Check if we hit max iterations
                if self._iteration >= self.config.max_iterations:
                    self.on_max_iterations()

            finally:
                self._client = None

    async def _send_message(self, prompt: str) -> None:
        """Send a message to the agent.

        Disables input while sending, then re-enables after.

        Args:
            prompt: The message to send.
        """
        assert self._bridge is not None
        assert self._client is not None

        self._bridge.disable("Sending message...")
        self._bridge.status = "working"

        try:
            await self._client.query(prompt)
        finally:
            self._bridge.enable()

    async def _process_turn(self) -> TurnResult:
        """Process one turn of messages from the agent.

        Returns:
            TurnResult indicating whether to exit or if interrupted.
        """
        assert self._bridge is not None
        assert self._client is not None

        self._last_assistant_text = ""
        self._is_turn_running = True
        self._bridge.status = "thinking"

        result = TurnResult()

        try:
            async for message in self._client.receive_response():
                self._printer.print_message(message)

                if isinstance(message, AssistantMessage):
                    text = extract_assistant_text(message)
                    self._last_assistant_text = text
                    self.on_assistant_message(message, text)
                    self._bridge.status = "working"

                if isinstance(message, ResultMessage):
                    self._save_session(
                        session_id=message.session_id,
                        num_turns=message.num_turns,
                        cost_usd=message.total_cost_usd,
                    )
                    self.on_result(message)

                    # Check if this was interrupted (user input is pending)
                    if self._pending_input is not None:
                        result.was_interrupted = True
                    elif self.is_complete(message):
                        self._completed = True
                        self.on_complete()
                        result.should_exit = True

        except Exception:
            if self._pending_input is not None:
                # Interrupted by user input
                result.was_interrupted = True
            else:
                raise
        finally:
            self._is_turn_running = False

        return result

    async def _get_next_prompt(self, was_interrupted: bool) -> str | None:
        """Determine the next prompt to send to the agent.

        User questions are handled by the ask_user MCP tool (which blocks
        Claude's turn until the user responds), so this method no longer
        needs LLM-based question detection.

        Args:
            was_interrupted: Whether the previous turn was interrupted.

        Returns:
            The next prompt to send, or None if the loop should exit.
        """
        assert self._bridge is not None

        # If we have pending user input (from interrupt or typed while idle)
        if self._pending_input is not None:
            prompt = self._pending_input
            self._pending_input = None
            return prompt

        # Check if we should wait before continuing
        idle_interval = self.get_idle_interval()
        if idle_interval is not None and idle_interval > 0:
            prompt = await self._wait_idle(idle_interval)
            if prompt is not None:
                return prompt

        # Return continuation prompt
        return self.get_continuation_prompt()

    async def _wait_idle(self, interval: int) -> str | None:
        """Wait for the idle interval, checking for user input.

        Args:
            interval: Seconds to wait.

        Returns:
            User input if received during wait, or None to continue.
        """
        assert self._bridge is not None

        self._bridge.status = "idle"
        self._bridge.show_lifecycle_message(
            f"Waiting {interval}s before continuing...", "info"
        )

        elapsed = 0.0
        check_interval = 0.1

        while elapsed < interval:
            # Check for user input during wait
            if self._pending_input is not None:
                prompt = self._pending_input
                self._pending_input = None
                return prompt

            await asyncio.sleep(check_interval)
            elapsed += check_interval

        return None  # No input received, caller should use continuation prompt
