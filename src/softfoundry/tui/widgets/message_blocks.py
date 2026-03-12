"""Individual message widgets for the message stream.

Each SDK message type gets its own widget:
- TextMessage: plain assistant text
- ThinkingBlock: collapsible thinking content
- ToolBlock: collapsible tool use + result (matched by tool_use_id)
- QuestionBlock: highlighted question from agent
- UserMessageBlock: user input display
- ResultBlock: turn completion summary
- SystemBlock: system message display
- LifecycleMessage: agent lifecycle events (completed, error, etc.)
"""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Markdown, Static


# ─── Helper ──────────────────────────────────────────────────────────────────


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate text with ellipsis."""
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _escape_markup(text: str) -> str:
    """Escape Rich markup characters for safe display in Textual."""
    return text.replace("[", r"\[")


# ─── Text Message ────────────────────────────────────────────────────────────


def _preserve_newlines(text: str) -> str:
    """Convert single newlines to markdown hard breaks.

    In standard markdown, a single newline is a soft break (collapsed
    to a space). LLM output often uses single newlines for visual line
    breaks. This converts them to hard breaks (two trailing spaces +
    newline) while preserving:
    - Double newlines (paragraph breaks) — left as-is
    - Content inside fenced code blocks (``` ... ```) — left as-is
    """
    import re

    parts = re.split(r"(```[\s\S]*?```)", text)
    result: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Inside a fenced code block — leave as-is
            result.append(part)
        else:
            # Outside code blocks — add trailing spaces for hard breaks
            # Replace single \n (not part of \n\n) with "  \n"
            processed = re.sub(r"(?<!\n)\n(?!\n)", "  \n", part)
            result.append(processed)
    return "".join(result)


class TextMessage(Markdown):
    """Assistant text output rendered as markdown.

    Uses Textual's Markdown widget which creates a subtree of child
    widgets (MarkdownParagraph, MarkdownFence, etc.) for proper
    block-level layout, syntax-highlighted code blocks, and tables.
    """

    DEFAULT_CSS = """
    TextMessage {
        padding: 0;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, text: str, **kwargs: Any) -> None:
        super().__init__(_preserve_newlines(text), **kwargs)
        self.add_class("message-block", "text-message")


# ─── User Message ────────────────────────────────────────────────────────────


class UserMessageBlock(Static):
    """User input display."""

    def __init__(self, text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._text = text
        self.add_class("message-block", "user-message")

    def render(self) -> str:
        return f"[bold]You:[/bold] {_escape_markup(self._text)}"


# ─── Collapsible Base ────────────────────────────────────────────────────────


class CollapsibleBlock(Widget):
    """Base class for collapsible content blocks.

    Subclasses implement `render_header()` and `render_content()`.
    Click on header toggles collapsed state.
    """

    collapsed: reactive[bool] = reactive(True)

    DEFAULT_CSS = ""

    def __init__(self, initially_collapsed: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._initial_collapsed = initially_collapsed
        self.add_class("collapsible", "message-block")

    def on_mount(self) -> None:
        self.collapsed = self._initial_collapsed

    def compose(self) -> ComposeResult:
        yield Static(id="header", classes="collapsible-header")
        yield Static(id="content", classes="collapsible-content")

    def render_header(self) -> str:
        """Return header text. Override in subclasses."""
        return ""

    def render_content(self) -> str:
        """Return content text. Override in subclasses."""
        return ""

    def watch_collapsed(self, collapsed: bool) -> None:
        header = self.query_one("#header", Static)
        content = self.query_one("#content", Static)
        header.update(self.render_header())
        content.update(self.render_content())
        if collapsed:
            content.add_class("hidden")
        else:
            content.remove_class("hidden")

    def on_click(self, event: Any) -> None:
        # Only toggle if clicking the header area
        header = self.query_one("#header", Static)
        if header in event.widget.ancestors_with_self:
            self.collapsed = not self.collapsed
            event.stop()


# ─── Thinking Block ──────────────────────────────────────────────────────────


class ThinkingBlock(CollapsibleBlock):
    """Collapsible thinking content.

    Collapsed by default. Shows truncated preview when collapsed.
    """

    def __init__(self, thinking_text: str, **kwargs: Any) -> None:
        super().__init__(initially_collapsed=True, **kwargs)
        self._thinking_text = thinking_text
        self.add_class("thinking-block")

    def render_header(self) -> str:
        arrow = "▶" if self.collapsed else "▼"
        if self.collapsed:
            preview = _truncate(self._thinking_text.replace("\n", " "), 80)
            return f"[steel_blue]{arrow} Thinking[/steel_blue] [dim]{_escape_markup(preview)}[/dim]"
        return f"[steel_blue]{arrow} Thinking[/steel_blue]"

    def render_content(self) -> str:
        return _escape_markup(self._thinking_text)


# ─── Tool Block ──────────────────────────────────────────────────────────────


class ToolBlock(CollapsibleBlock):
    """Collapsible tool use block with result.

    States:
    - Running: expanded, shows spinner, yellow header
    - Success: auto-collapsed, green checkmark, muted header
    - Error: stays expanded, red X, red header

    The tool_use_id links ToolUseBlock to its ToolResultBlock.
    """

    state: reactive[str] = reactive("running")  # "running", "success", "error"

    def __init__(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(initially_collapsed=False, **kwargs)
        self.add_class("tool-block")
        self.tool_use_id = tool_use_id
        self._tool_name = tool_name
        self._tool_input = tool_input
        self._tool_summary = self._format_tool_summary(tool_name, tool_input)
        self._result_content: str = ""
        self._result_is_error: bool = False
        self._result_summary: str = ""

    def render_header(self) -> str:
        arrow = "▶" if self.collapsed else "▼"

        if self.state == "running":
            return f"[yellow]{arrow} ⟳ Tool: {self._tool_name}[/yellow] [dim]{self._tool_summary}[/dim]"
        elif self.state == "success":
            return (
                f"[dim]{arrow} Tool: {self._tool_name} "
                f"{self._tool_summary} [green]✓[/green]"
                f"{' ' + self._result_summary if self._result_summary else ''}[/dim]"
            )
        else:  # error
            return f"[red]{arrow} Tool: {self._tool_name} {self._tool_summary} ✗[/red]"

    def render_content(self) -> str:
        parts: list[str] = []

        # Show input details
        if self._tool_input:
            input_str = self._format_tool_input_detail()
            if input_str:
                parts.append(f"[dim]{_escape_markup(input_str)}[/dim]")

        # Show result if available
        if self._result_content:
            if self._result_is_error:
                parts.append(f"[red]{_escape_markup(self._result_content)}[/red]")
            else:
                content = _truncate(self._result_content, 1000)
                parts.append(f"[dim]{_escape_markup(content)}[/dim]")

        return "\n".join(parts) if parts else ""

    def set_result(
        self,
        content: str,
        is_error: bool,
    ) -> None:
        """Update with tool result, triggering state change and auto-collapse."""
        self._result_content = content
        self._result_is_error = is_error

        if is_error:
            self._result_summary = ""
            self.state = "error"
            self.collapsed = False
        else:
            self._result_summary = self._format_result_summary(content)
            self.state = "success"
            self.collapsed = True

        # Force re-render
        self._refresh_display()

    def watch_state(self, state: str) -> None:
        # Note: _refresh_display() is already called explicitly in set_result().
        # This watcher only handles programmatic state changes outside set_result().
        pass

    def _refresh_display(self) -> None:
        """Force header and content to re-render."""
        try:
            header = self.query_one("#header", Static)
            content = self.query_one("#content", Static)
            header.update(self.render_header())
            content.update(self.render_content())
            if self.collapsed:
                content.add_class("hidden")
            else:
                content.remove_class("hidden")
        except Exception:
            pass  # Widget may not be mounted yet

    @staticmethod
    def _format_tool_summary(tool_name: str, tool_input: dict[str, Any]) -> str:
        """Format a brief one-line summary of the tool call."""
        if tool_name in ("Read", "Write", "Edit"):
            path = tool_input.get("file_path") or tool_input.get("filePath", "")
            return f"({path})" if path else ""
        elif tool_name == "Bash":
            cmd = tool_input.get("command", "")
            if len(cmd) > 60:
                cmd = cmd[:60] + "..."
            return f"({cmd})" if cmd else ""
        elif tool_name in ("Glob", "Grep"):
            pattern = tool_input.get("pattern", "")
            return f"({pattern})" if pattern else ""
        elif tool_name == "Task":
            desc = tool_input.get("description", "")
            return f"({desc})" if desc else ""
        elif tool_name == "TodoWrite":
            todos = tool_input.get("todos", [])
            return f"({len(todos)} items)"
        else:
            # Generic: first key-value
            if tool_input:
                first_key = next(iter(tool_input))
                first_val = str(tool_input[first_key])
                if len(first_val) > 40:
                    first_val = first_val[:40] + "..."
                return f"({first_key}={first_val})"
            return ""

    def _format_tool_input_detail(self) -> str:
        """Format tool input for expanded view."""
        try:
            return json.dumps(self._tool_input, indent=2, default=str)
        except (TypeError, ValueError):
            return str(self._tool_input)

    @staticmethod
    def _format_result_summary(content: str) -> str:
        """Format a brief summary of the result for the collapsed header."""
        if not content or not content.strip():
            return ""
        lines = content.strip().split("\n")
        num_bytes = len(content.encode("utf-8"))
        if num_bytes >= 1024:
            size_str = f"{num_bytes / 1024:.1f}KB"
        else:
            size_str = f"{num_bytes}B"
        if len(lines) > 1:
            return f"[dim]({size_str}, {len(lines)} lines)[/dim]"
        elif num_bytes > 50:
            return f"[dim]({size_str})[/dim]"
        return ""


# ─── Question Block ──────────────────────────────────────────────────────────


class QuestionBlock(Widget):
    """Highlighted question from the agent.

    Visually distinct with a cyan border to signal the agent is waiting.
    """

    def __init__(
        self,
        question: str,
        options: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._question = question
        self._options = options
        self.add_class("message-block", "question-block")

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold cyan]Question:[/bold cyan] {_escape_markup(self._question)}",
            classes="question-text",
        )
        if self._options:
            for i, opt in enumerate(self._options, 1):
                yield Static(
                    f"  [cyan]{i}.[/cyan] {_escape_markup(opt)}",
                    classes="question-option",
                )
            yield Static(
                "Type a number to select, or type your own answer.",
                classes="question-hint",
            )


# ─── Result Block ────────────────────────────────────────────────────────────


class ResultBlock(Widget):
    """Turn completion summary with rule and stats."""

    def __init__(
        self,
        is_error: bool,
        subtype: str,
        duration_ms: int | None = None,
        cost_usd: float | None = None,
        num_turns: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._is_error = is_error
        self._subtype = subtype
        self._duration_ms = duration_ms
        self._cost_usd = cost_usd
        self._num_turns = num_turns
        self.add_class("message-block", "result-block")

    def compose(self) -> ComposeResult:
        if self._is_error:
            yield Static(
                "─── [bold red]Error[/bold red] ───",
                classes="result-rule-error",
            )
        else:
            yield Static(
                "─── [bold green]Complete[/bold green] ───",
                classes="result-rule-success",
            )

        # Info line
        parts = [f"[bold]{self._subtype}[/bold]"]
        if self._duration_ms:
            parts.append(f"Duration: {self._duration_ms / 1000:.1f}s")
        if self._cost_usd is not None:
            parts.append(f"Cost: ${self._cost_usd:.4f}")
        if self._num_turns:
            parts.append(f"Turns: {self._num_turns}")
        yield Static(" | ".join(parts), classes="result-info")


# ─── System Block ────────────────────────────────────────────────────────────


class SystemBlock(Static):
    """System message display."""

    def __init__(self, subtype: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._subtype = subtype
        self.add_class("message-block", "system-block")

    def render(self) -> str:
        return f"[medium_purple]System:[/medium_purple] {self._subtype}"


# ─── Lifecycle Message ───────────────────────────────────────────────────────


class SessionSeparator(Static):
    """Visual separator between agent sessions in persistent TUI mode."""

    def __init__(self, label: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._label = label
        self.add_class("message-block", "session-separator")

    def render(self) -> str:
        if self._label:
            return f"[bold $accent]{'─' * 3} {_escape_markup(self._label)} {'─' * 40}[/bold $accent]"
        return f"[dim]{'─' * 50}[/dim]"


class LifecycleMessage(Static):
    """Agent lifecycle events (completed, error, warning, info)."""

    def __init__(
        self,
        text: str,
        level: str = "info",
        **kwargs: Any,
    ) -> None:
        """Create a lifecycle message.

        Args:
            text: The message text.
            level: One of "success", "error", "warning", "info".
        """
        super().__init__(**kwargs)
        self._text = text
        self._level = level
        self.add_class("message-block", f"lifecycle-{level}")

    def render(self) -> str:
        return _escape_markup(self._text)
