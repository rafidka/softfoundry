"""Main Textual App for softfoundry agent TUI.

Composes the split-pane layout: message stream (center), sidebar (right),
input area (bottom), status bar (footer).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage as SDKResultMessage,
    SystemMessage as SDKSystemMessage,
    TextBlock,
    ThinkingBlock as SDKThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import TextSelected
from textual.reactive import reactive

from softfoundry.tui.widgets.input_area import InputArea
from softfoundry.tui.widgets.message_blocks import (
    LifecycleMessage,
    QuestionBlock,
    ResultBlock,
    SessionSeparator,
    SystemBlock,
    TextMessage,
    ThinkingBlock,
    ToolBlock,
    UserMessageBlock,
)
from softfoundry.tui.widgets.message_stream import MessageStream
from softfoundry.tui.widgets.sidebar import EpicIssue, Sidebar, TaskInfo
from softfoundry.tui.widgets.status_bar import StatusBar

# Minimum terminal width to show sidebar by default
SIDEBAR_MIN_WIDTH = 100


class SoftFoundryApp(App[None]):
    """Main TUI application for softfoundry agents.

    Layout:
    ┌─────────────────────────┬──────────┐
    │                         │          │
    │                         │          │
    │     Message Stream      │          │
    │                         │ Sidebar  │
    │                         │          │
    ├─────────────────────────│          │
    │     Input Area          │          │
    ├────────────────────────────────────│
    │            Status Bar              │
    └────────────────────────────────────┘
    """

    CSS_PATH = "styles/app.tcss"

    BINDINGS = [
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=False),
        Binding("ctrl+d", "quit", "Exit", show=False),
        Binding("end", "scroll_to_bottom", "Jump to bottom", show=False),
    ]

    status: reactive[str] = reactive("idle")

    def __init__(
        self,
        agent_type: str = "",
        agent_name: str = "",
        project: str = "",
        epic_number: int | None = None,
        on_input: Callable[[str], None] | None = None,
        agent_coroutine: Callable[[], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the TUI app.

        Args:
            agent_type: Agent type (e.g., "manager", "programmer").
            agent_name: Agent instance name.
            project: Project namespace.
            epic_number: Epic issue number (for sidebar).
            on_input: Callback when user submits input.
            agent_coroutine: Async function to run the agent loop.
        """
        super().__init__(**kwargs)
        self._agent_type = agent_type
        self._agent_name = agent_name
        self._project = project
        self._epic_number = epic_number
        self._on_input = on_input
        self._agent_coroutine = agent_coroutine
        self._sidebar_user_hidden = False  # User explicitly hid sidebar

        # Message dispatch state
        self.verbosity: str = "medium"
        self._pending_tools: dict[str, str] = {}  # tool_use_id -> tool_name

    def compose(self) -> ComposeResult:
        # Horizontal split: left column (stream + input) | sidebar
        # Input sits inside the left column so it doesn't extend under sidebar
        with Horizontal(id="main-container"):
            with Vertical(id="left-column"):
                yield MessageStream()
                yield InputArea()
            yield Sidebar(
                agent_type=self._agent_type,
                project=self._project,
                epic_number=self._epic_number,
            )
        yield StatusBar(
            agent_type=self._agent_type,
            project=self._project,
        )

    def on_mount(self) -> None:
        """Called when the app is ready."""
        # Auto-hide sidebar on narrow terminals
        self._update_sidebar_visibility()
        # Start the agent loop if provided
        if self._agent_coroutine:
            self.run_agent_loop()

    def on_resize(self) -> None:
        """Handle terminal resize — auto-hide/show sidebar."""
        self._update_sidebar_visibility()

    def _update_sidebar_visibility(self) -> None:
        """Show/hide sidebar based on terminal width and user preference."""
        try:
            sidebar = self.query_one(Sidebar)
            if self._sidebar_user_hidden:
                sidebar.add_class("hidden")
            elif self.size.width < SIDEBAR_MIN_WIDTH:
                sidebar.add_class("hidden")
            else:
                sidebar.remove_class("hidden")
        except Exception:
            pass

    # ─── Actions ─────────────────────────────────────────────────────────────

    def action_toggle_sidebar(self) -> None:
        """Toggle sidebar visibility."""
        try:
            sidebar = self.query_one(Sidebar)
            if sidebar.has_class("hidden"):
                sidebar.remove_class("hidden")
                self._sidebar_user_hidden = False
            else:
                sidebar.add_class("hidden")
                self._sidebar_user_hidden = True
        except Exception:
            pass

    def action_scroll_to_bottom(self) -> None:
        """Jump to bottom of message stream."""
        stream = self.query_one(MessageStream)
        stream.scroll_to_bottom()

    # ─── Selection ────────────────────────────────────────────────────────────

    def _on_text_selected(self, event: TextSelected) -> None:
        """Auto-copy selected text to clipboard on mouse selection."""
        text = self.screen.get_selected_text()
        if text:
            self.copy_to_clipboard(text)
            self.notify("Copied to clipboard", timeout=3)

    # ─── Input Handling ──────────────────────────────────────────────────────

    @on(InputArea.Submitted)
    def _on_input_submitted(self, event: InputArea.Submitted) -> None:
        """Handle user input submission."""
        if self._on_input and event.text.strip():
            # Add user message to stream
            stream = self.query_one(MessageStream)
            stream.add_block(UserMessageBlock(event.text))
            # Notify callback
            self._on_input(event.text)

    # ─── Agent Loop Worker ───────────────────────────────────────────────────

    @work(exclusive=True, thread=False, name="agent-loop")
    async def run_agent_loop(self) -> None:
        """Run the agent coroutine as a Textual worker."""
        if self._agent_coroutine:
            try:
                await self._agent_coroutine()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.add_lifecycle_message(f"Agent error: {e}", "error")

    # ─── Public API ────────────────────────────────────────────────────────────

    def add_message(self, message: Any) -> None:
        """Translate an SDK message into TUI widget(s).

        Dispatches by message type, creating the appropriate widgets.

        Args:
            message: Any claude_agent_sdk message type.
        """
        if isinstance(message, AssistantMessage):
            self._handle_assistant_message(message)
        elif isinstance(message, UserMessage):
            self._handle_user_message(message)
        elif isinstance(message, SDKSystemMessage):
            self._handle_system_message(message)
        elif isinstance(message, SDKResultMessage):
            self._handle_result_message(message)

    def _handle_assistant_message(self, message: AssistantMessage) -> None:
        """Process an assistant message — dispatch each content block."""
        for block in message.content:
            if isinstance(block, TextBlock):
                if block.text.strip():
                    self.add_text_message(block.text)
            elif isinstance(block, SDKThinkingBlock):
                if self.verbosity != "minimal":
                    self.add_thinking_block(block.thinking)
            elif isinstance(block, ToolUseBlock):
                self._handle_tool_use(block)

    def _handle_tool_use(self, block: ToolUseBlock) -> None:
        """Add a tool use block in running state."""
        tool_input = block.input if isinstance(block.input, dict) else {}
        self._pending_tools[block.id] = block.name

        if self.verbosity == "minimal":
            self.add_lifecycle_message(f"Tool: {block.name}", "info")
        else:
            self.add_tool_block(block.id, block.name, tool_input)

    def _handle_user_message(self, message: UserMessage) -> None:
        """Process a user message."""
        if self.verbosity == "minimal":
            return

        content = message.content
        if isinstance(content, str):
            self.add_text_message(f"User: {content}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    self._handle_tool_result(block)
                elif isinstance(block, TextBlock):
                    if block.text.strip():
                        self.add_text_message(f"User: {block.text}")

    def _handle_tool_result(self, block: ToolResultBlock) -> None:
        """Match a tool result to its tool use block."""
        if self.verbosity == "minimal":
            return

        tool_use_id = block.tool_use_id
        is_error = block.is_error or False
        content_str = self._format_tool_result_content(block.content)

        self.update_tool_result(tool_use_id, content_str, is_error)

        # Clean up tracking
        self._pending_tools.pop(tool_use_id, None)

    def _handle_system_message(self, message: SDKSystemMessage) -> None:
        """Process a system message."""
        if self.verbosity == "minimal":
            return
        # Skip init messages — they're SDK initialization noise
        if message.subtype == "init":
            return
        self.add_system_block(message.subtype)

    def _handle_result_message(self, message: SDKResultMessage) -> None:
        """Process a result message (turn completion)."""
        self.add_result_block(
            message.is_error,
            message.subtype,
            message.duration_ms,
            message.total_cost_usd,
            message.num_turns,
        )

    @staticmethod
    def _format_tool_result_content(
        content: str | list[dict[str, Any]] | Any,
    ) -> str:
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

    def update_status(self, status: str) -> None:
        """Update the global agent status (sidebar + status bar + input)."""
        self.status = status
        try:
            sidebar = self.query_one(Sidebar)
            sidebar.status = status
        except Exception:
            pass
        try:
            status_bar = self.query_one(StatusBar)
            status_bar.status = status
        except Exception:
            pass

    def enable_input(self) -> None:
        """Enable the input area."""
        try:
            input_area = self.query_one(InputArea)
            input_area.enable()
        except Exception:
            pass

    def disable_input(self, message: str = "Please wait...") -> None:
        """Disable the input area with a message."""
        try:
            input_area = self.query_one(InputArea)
            input_area.disable(message)
        except Exception:
            pass

    def set_question_mode(self, enabled: bool) -> None:
        """Toggle question mode on the input area."""
        try:
            input_area = self.query_one(InputArea)
            input_area.question_mode = enabled
        except Exception:
            pass

    def add_text_message(self, text: str) -> None:
        """Add an assistant text message to the stream."""
        stream = self.query_one(MessageStream)
        stream.add_block(TextMessage(text))

    def add_thinking_block(self, thinking_text: str) -> None:
        """Add a collapsible thinking block."""
        stream = self.query_one(MessageStream)
        stream.add_block(ThinkingBlock(thinking_text))

    def add_tool_block(
        self, tool_use_id: str, tool_name: str, tool_input: dict[str, Any]
    ) -> ToolBlock:
        """Add a tool use block (in running state). Returns it for later update."""
        block = ToolBlock(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        stream = self.query_one(MessageStream)
        stream.add_block(block)
        return block

    def update_tool_result(
        self, tool_use_id: str, content: str, is_error: bool
    ) -> None:
        """Update a tool block with its result by tool_use_id."""
        try:
            stream = self.query_one(MessageStream)
            for widget in stream.children:
                if isinstance(widget, ToolBlock) and widget.tool_use_id == tool_use_id:
                    widget.set_result(content, is_error)
                    return
        except Exception:
            pass

    def add_question_block(
        self, question: str, options: list[str] | None = None
    ) -> None:
        """Add a question block to the stream and enter question mode."""
        stream = self.query_one(MessageStream)
        stream.add_block(QuestionBlock(question, options))
        self.set_question_mode(True)

    def clear_question_mode(self) -> None:
        """Exit question mode (after user answers)."""
        self.set_question_mode(False)

    def add_result_block(
        self,
        is_error: bool,
        subtype: str,
        duration_ms: int | None = None,
        cost_usd: float | None = None,
        num_turns: int | None = None,
    ) -> None:
        """Add a result block (turn completion summary)."""
        stream = self.query_one(MessageStream)
        stream.add_block(
            ResultBlock(
                is_error=is_error,
                subtype=subtype,
                duration_ms=duration_ms,
                cost_usd=cost_usd,
                num_turns=num_turns,
            )
        )
        # Update sidebar and status bar
        if num_turns:
            try:
                sidebar = self.query_one(Sidebar)
                sidebar.update_session(num_turns, cost_usd or 0.0)
            except Exception:
                pass
            try:
                status_bar = self.query_one(StatusBar)
                status_bar.turns = num_turns
                status_bar.cost_usd = cost_usd or 0.0
            except Exception:
                pass

    def update_session_stats(self, turns: int, cost_usd: float) -> None:
        """Update sidebar and status bar session stats without adding a stream widget."""
        try:
            sidebar = self.query_one(Sidebar)
            sidebar.update_session(turns, cost_usd)
        except Exception:
            pass
        try:
            status_bar = self.query_one(StatusBar)
            status_bar.turns = turns
            status_bar.cost_usd = cost_usd
        except Exception:
            pass

    def add_system_block(self, subtype: str) -> None:
        """Add a system message block."""
        stream = self.query_one(MessageStream)
        stream.add_block(SystemBlock(subtype))

    def add_lifecycle_message(self, text: str, level: str = "info") -> None:
        """Add a lifecycle message (success, error, warning, info)."""
        stream = self.query_one(MessageStream)
        stream.add_block(LifecycleMessage(text, level))

    def add_session_separator(self, label: str = "") -> None:
        """Add a visual separator between sessions."""
        stream = self.query_one(MessageStream)
        stream.add_block(SessionSeparator(label))

    def update_task_info(
        self,
        issue_number: int | None = None,
        title: str = "",
        pr_number: int | None = None,
    ) -> None:
        """Update the sidebar task info."""
        try:
            sidebar = self.query_one(Sidebar)
            sidebar.update_task(
                TaskInfo(
                    issue_number=issue_number,
                    title=title,
                    pr_number=pr_number,
                )
            )
        except Exception:
            pass

    def update_epic_progress(self, issues: list[EpicIssue]) -> None:
        """Update the sidebar epic progress."""
        try:
            sidebar = self.query_one(Sidebar)
            sidebar.update_epic(issues)
        except Exception:
            pass
