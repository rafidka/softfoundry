"""Bridge between the Agent base class and the Textual TUI.

AgentBridge adapts the Agent's expected interfaces (InteractiveInput-like
and MessagePrinter-like) into Textual app method calls. It translates
claude_agent_sdk message types into TUI widget updates.

The bridge uses `app.call_from_thread()` when called from a worker thread,
or direct method calls when on the main thread.
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from softfoundry.tui.app import SoftFoundryApp
from softfoundry.tui.widgets.sidebar import EpicIssue


class AgentBridge:
    """Bridges the Agent loop and the Textual TUI.

    Provides two interfaces:
    1. InteractiveInput-compatible: status, enable, disable, stop
    2. MessagePrinter-compatible: print_message()

    Also provides methods for question handling and sidebar updates.

    All TUI mutations go through the app's thread-safe call mechanisms.
    """

    def __init__(self, app: SoftFoundryApp) -> None:
        self._app = app
        self._status = "idle"
        self._enabled = True

        # Track pending tool blocks by tool_use_id for result matching
        self._pending_tools: dict[str, str] = {}  # tool_use_id -> tool_name

        # Track verbosity (for filtering)
        self._verbosity = "medium"

    # ─── InteractiveInput-compatible interface ───────────────────────────────

    @property
    def status(self) -> str:
        """Get the current agent status."""
        return self._status

    @status.setter
    def status(self, value: str) -> None:
        """Set agent status, updating all TUI status indicators."""
        self._status = value
        self._call_on_app("update_status", value)

    @property
    def enabled(self) -> bool:
        """Check if input is currently enabled."""
        return self._enabled

    def enable(self) -> None:
        """Enable the input area."""
        self._enabled = True
        self._call_on_app("enable_input")

    def disable(self, message: str = "Please wait...") -> None:
        """Disable the input area with a message."""
        self._enabled = False
        self._call_on_app("disable_input", message)

    def stop(self) -> None:
        """Stop the TUI (exit the app)."""
        self._call_on_app("exit")

    # ─── MessagePrinter-compatible interface ─────────────────────────────────

    def print_message(self, message: Any) -> None:
        """Translate an SDK message into TUI widget(s).

        Dispatches by message type, creating the appropriate widgets.

        Args:
            message: Any claude_agent_sdk message type.
        """
        if isinstance(message, AssistantMessage):
            self._handle_assistant_message(message)
        elif isinstance(message, UserMessage):
            self._handle_user_message(message)
        elif isinstance(message, SystemMessage):
            self._handle_system_message(message)
        elif isinstance(message, ResultMessage):
            self._handle_result_message(message)

    def _handle_assistant_message(self, message: AssistantMessage) -> None:
        """Process an assistant message — dispatch each content block."""
        for block in message.content:
            if isinstance(block, TextBlock):
                if block.text.strip():
                    self._call_on_app("add_text_message", block.text)
            elif isinstance(block, ThinkingBlock):
                if self._verbosity != "minimal":
                    self._call_on_app("add_thinking_block", block.thinking)
            elif isinstance(block, ToolUseBlock):
                self._handle_tool_use(block)

    def _handle_tool_use(self, block: ToolUseBlock) -> None:
        """Add a tool use block in running state."""
        tool_input = block.input if isinstance(block.input, dict) else {}
        self._pending_tools[block.id] = block.name

        if self._verbosity == "minimal":
            # At minimal, just show tool name as text
            self._call_on_app(
                "add_lifecycle_message",
                f"Tool: {block.name}",
                "info",
            )
        else:
            self._call_on_app("add_tool_block", block.id, block.name, tool_input)

    def _handle_user_message(self, message: UserMessage) -> None:
        """Process a user message."""
        if self._verbosity == "minimal":
            return

        content = message.content
        if isinstance(content, str):
            self._call_on_app("add_text_message", f"User: {content}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    self._handle_tool_result(block)
                elif isinstance(block, TextBlock):
                    if block.text.strip():
                        self._call_on_app("add_text_message", f"User: {block.text}")

    def _handle_tool_result(self, block: ToolResultBlock) -> None:
        """Match a tool result to its tool use block."""
        if self._verbosity == "minimal":
            return

        tool_use_id = block.tool_use_id
        is_error = block.is_error or False
        content_str = self._format_tool_result_content(block.content)

        self._call_on_app("update_tool_result", tool_use_id, content_str, is_error)

        # Clean up tracking
        self._pending_tools.pop(tool_use_id, None)

    def _handle_system_message(self, message: SystemMessage) -> None:
        """Process a system message."""
        if self._verbosity == "minimal":
            return
        # Skip init messages — they're SDK initialization noise
        if message.subtype == "init":
            return
        self._call_on_app("add_system_block", message.subtype)

    def _handle_result_message(self, message: ResultMessage) -> None:
        """Process a result message (turn completion)."""
        self._call_on_app(
            "add_result_block",
            message.is_error,
            message.subtype,
            message.duration_ms,
            message.total_cost_usd,
            message.num_turns,
        )

    # ─── Question Handling ───────────────────────────────────────────────────

    def show_question(self, question: str, options: list[str] | None) -> None:
        """Display a question in the stream and enter question mode."""
        self._call_on_app("add_question_block", question, options)

    def clear_question(self) -> None:
        """Exit question mode after user answers."""
        self._call_on_app("clear_question_mode")

    # ─── Sidebar Updates ────────────────────────────────────────────────────

    def update_session_info(self, turns: int, cost_usd: float | None) -> None:
        """Update session statistics in sidebar and status bar (no stream widget)."""
        self._call_on_app("update_session_stats", turns, cost_usd or 0.0)

    def update_task_info(
        self,
        issue_number: int | None = None,
        title: str = "",
        pr_number: int | None = None,
    ) -> None:
        """Update the current task info in the sidebar."""
        self._call_on_app("update_task_info", issue_number, title, pr_number)

    def update_epic_progress(self, issues: list[EpicIssue]) -> None:
        """Update epic progress in the sidebar."""
        self._call_on_app("update_epic_progress", issues)

    # ─── Lifecycle Messages ──────────────────────────────────────────────────

    def show_lifecycle_message(self, text: str, level: str = "info") -> None:
        """Show a lifecycle event (completed, error, warning, info)."""
        self._call_on_app("add_lifecycle_message", text, level)

    # ─── Console compatibility ───────────────────────────────────────────────

    @property
    def console(self) -> "_BridgeConsole":
        """Provide a console-like interface for backward compatibility.

        This allows code that does `self._printer.console.print(...)` to
        work through the bridge.
        """
        return _BridgeConsole(self)

    # ─── Internal ────────────────────────────────────────────────────────────

    def _call_on_app(self, method_name: str, *args: Any) -> None:
        """Call a method on the app safely.

        The agent loop runs as an async worker (same event loop as Textual),
        so we call methods directly. For thread-based workers, we'd need
        call_from_thread, but our workers are async (thread=False).
        """
        method = getattr(self._app, method_name)
        try:
            method(*args)
        except Exception:
            # Fallback: try call_from_thread in case we're in a thread
            try:
                self._app.call_from_thread(method, *args)
            except Exception:
                pass

    @staticmethod
    def _format_tool_result_content(content: str | list[dict[str, Any]] | Any) -> str:
        """Format tool result content to a string."""
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    else:
                        parts.append(json.dumps(item, default=str))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        elif content is None:
            return ""
        return str(content)


class _BridgeConsole:
    """Minimal console-like interface for backward compatibility.

    Allows `bridge.console.print(...)` to route through the TUI
    as lifecycle messages.
    """

    def __init__(self, bridge: AgentBridge) -> None:
        self._bridge = bridge

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Print text through the TUI as a lifecycle message.

        Strips Rich markup for display as plain lifecycle text.
        """
        text = " ".join(str(a) for a in args)
        # Determine level from common patterns
        level = "info"
        text_lower = text.lower()
        if "error" in text_lower or "failed" in text_lower:
            level = "error"
        elif "completed" in text_lower or "success" in text_lower:
            level = "success"
        elif "warning" in text_lower or "reaching" in text_lower:
            level = "warning"
        elif "interrupted" in text_lower:
            level = "warning"

        self._bridge.show_lifecycle_message(text, level)
