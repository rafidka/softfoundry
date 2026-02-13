"""Output utilities for formatting and printing agent messages."""

import json
from enum import Enum
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
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text


class Verbosity(Enum):
    """Verbosity levels for message output."""

    MINIMAL = "minimal"
    MEDIUM = "medium"
    VERBOSE = "verbose"


class MessagePrinter:
    """Handles printing of agent messages with configurable verbosity."""

    def __init__(
        self,
        verbosity: Verbosity = Verbosity.MEDIUM,
        console: Console | None = None,
    ) -> None:
        """Initialize the message printer.

        Args:
            verbosity: The verbosity level for output.
            console: Optional Rich console instance. Creates one if not provided.
        """
        self.verbosity = verbosity
        self.console = console or Console()

    def print_message(self, message: Any) -> None:
        """Print a message based on its type.

        Args:
            message: The message to print (any SDK message type).
        """
        if isinstance(message, AssistantMessage):
            self._print_assistant_message(message)
        elif isinstance(message, UserMessage):
            self._print_user_message(message)
        elif isinstance(message, SystemMessage):
            self._print_system_message(message)
        elif isinstance(message, ResultMessage):
            self._print_result_message(message)
        else:
            # Handle unknown message types
            if self.verbosity == Verbosity.VERBOSE:
                self.console.print(
                    f"[dim]Unknown message type: {type(message).__name__}[/dim]"
                )

    def _print_user_message(self, message: UserMessage) -> None:
        """Print a user message."""
        if self.verbosity == Verbosity.MINIMAL:
            return

        content = message.content
        if isinstance(content, str):
            self.console.print(f"[bold blue]User:[/bold blue] {escape(content)}")
        elif isinstance(content, list):
            self.console.print("[bold blue]User:[/bold blue]")
            for block in content:
                self._print_content_block(block)

    def _print_assistant_message(self, message: AssistantMessage) -> None:
        """Print an assistant message with all its content blocks."""
        for block in message.content:
            self._print_content_block(block)

    def _print_content_block(self, block: Any) -> None:
        """Print a single content block based on its type."""
        if isinstance(block, TextBlock):
            self._print_text_block(block)
        elif isinstance(block, ThinkingBlock):
            self._print_thinking_block(block)
        elif isinstance(block, ToolUseBlock):
            self._print_tool_use_block(block)
        elif isinstance(block, ToolResultBlock):
            self._print_tool_result_block(block)
        else:
            # Fallback for unknown block types
            if self.verbosity == Verbosity.VERBOSE:
                self.console.print(
                    f"[dim]Unknown block type: {type(block).__name__}[/dim]"
                )

    def _print_text_block(self, block: TextBlock) -> None:
        """Print a text block."""
        if block.text.strip():
            self.console.print(escape(block.text))

    def _print_thinking_block(self, block: ThinkingBlock) -> None:
        """Print a thinking block."""
        if self.verbosity == Verbosity.MINIMAL:
            return

        if self.verbosity == Verbosity.MEDIUM:
            # Show truncated thinking
            thinking = block.thinking
            if len(thinking) > 200:
                thinking = thinking[:200] + "..."
            self.console.print(
                Panel(
                    escape(thinking),
                    title="[italic cyan]Thinking[/italic cyan]",
                    border_style="cyan",
                    padding=(0, 1),
                )
            )
        else:  # VERBOSE
            self.console.print(
                Panel(
                    escape(block.thinking),
                    title="[italic cyan]Thinking[/italic cyan]",
                    border_style="cyan",
                    padding=(0, 1),
                )
            )

    def _print_tool_use_block(self, block: ToolUseBlock) -> None:
        """Print a tool use block with appropriate detail level."""
        tool_name = block.name
        tool_input = block.input

        if self.verbosity == Verbosity.MINIMAL:
            self.console.print(f"[yellow]Tool:[/yellow] {tool_name}")
            return

        # Build the tool info string
        tool_info = self._format_tool_input(tool_name, tool_input)

        if self.verbosity == Verbosity.MEDIUM:
            self.console.print(f"[yellow]Tool:[/yellow] {tool_name} {tool_info}")
        else:  # VERBOSE
            self.console.print(f"[yellow]Tool:[/yellow] {tool_name}")
            self.console.print(
                Panel(
                    self._format_json(tool_input),
                    title="[dim]Input[/dim]",
                    border_style="yellow",
                    padding=(0, 1),
                )
            )

    def _format_tool_input(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Format tool input for medium verbosity display.

        Args:
            tool_name: Name of the tool.
            tool_input: The tool's input parameters.

        Returns:
            A formatted string with key parameters.
        """
        # Tool-specific formatting for common tools
        if tool_name == "Read":
            file_path = tool_input.get("file_path", tool_input.get("filePath", ""))
            return f"[dim]({escape(file_path)})[/dim]"

        elif tool_name == "Write":
            file_path = tool_input.get("file_path", tool_input.get("filePath", ""))
            return f"[dim]({escape(file_path)})[/dim]"

        elif tool_name == "Edit":
            file_path = tool_input.get("file_path", tool_input.get("filePath", ""))
            return f"[dim]({escape(file_path)})[/dim]"

        elif tool_name == "Bash":
            command = tool_input.get("command", "")
            if len(command) > 60:
                command = command[:60] + "..."
            return f"[dim]({escape(command)})[/dim]"

        elif tool_name == "Glob":
            pattern = tool_input.get("pattern", "")
            return f"[dim]({escape(pattern)})[/dim]"

        elif tool_name == "Grep":
            pattern = tool_input.get("pattern", "")
            return f"[dim]({escape(pattern)})[/dim]"

        elif tool_name == "Task":
            description = tool_input.get("description", "")
            return f"[dim]({escape(description)})[/dim]"

        elif tool_name == "TodoWrite":
            todos = tool_input.get("todos", [])
            return f"[dim]({len(todos)} items)[/dim]"

        else:
            # Generic: show first key-value pair if available
            if tool_input:
                first_key = next(iter(tool_input))
                first_value = str(tool_input[first_key])
                if len(first_value) > 40:
                    first_value = first_value[:40] + "..."
                return f"[dim]({first_key}={escape(first_value)})[/dim]"
            return ""

    def _print_tool_result_block(self, block: ToolResultBlock) -> None:
        """Print a tool result block."""
        if self.verbosity == Verbosity.MINIMAL:
            return

        is_error = block.is_error or False
        status = "[red]Error[/red]" if is_error else "[green]Success[/green]"

        if self.verbosity == Verbosity.MEDIUM:
            self.console.print(f"  [dim]->[/dim] {status}")
        else:  # VERBOSE
            content = block.content
            if content:
                content_str = self._format_tool_result_content(content)
                self.console.print(f"  [dim]->[/dim] {status}")
                if content_str.strip():
                    # Truncate very long results
                    if len(content_str) > 1000:
                        content_str = content_str[:1000] + "\n... (truncated)"
                    self.console.print(
                        Panel(
                            escape(content_str),
                            border_style="green" if not is_error else "red",
                            padding=(0, 1),
                        )
                    )
            else:
                self.console.print(f"  [dim]->[/dim] {status}")

    def _format_tool_result_content(self, content: str | list[dict[str, Any]]) -> str:
        """Format tool result content to a string."""
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            # Handle list of content blocks
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    else:
                        parts.append(json.dumps(item))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)

    def _print_system_message(self, message: SystemMessage) -> None:
        """Print a system message."""
        if self.verbosity == Verbosity.MINIMAL:
            return

        subtype = message.subtype

        if self.verbosity == Verbosity.MEDIUM:
            self.console.print(f"[magenta]System:[/magenta] {subtype}")
        else:  # VERBOSE
            self.console.print(f"[magenta]System:[/magenta] {subtype}")
            if message.data:
                self.console.print(
                    Panel(
                        self._format_json(message.data),
                        border_style="magenta",
                        padding=(0, 1),
                    )
                )

    def _print_result_message(self, message: ResultMessage) -> None:
        """Print a result message with summary information."""
        is_error = message.is_error
        subtype = message.subtype

        if is_error:
            status_text = Text("Error", style="bold red")
        else:
            status_text = Text("Complete", style="bold green")

        # Build summary info
        info_parts = [f"[bold]{subtype}[/bold]"]

        if message.duration_ms:
            duration_sec = message.duration_ms / 1000
            info_parts.append(f"Duration: {duration_sec:.1f}s")

        if message.total_cost_usd is not None:
            info_parts.append(f"Cost: ${message.total_cost_usd:.4f}")

        if message.num_turns:
            info_parts.append(f"Turns: {message.num_turns}")

        self.console.print()
        self.console.rule(status_text)

        if self.verbosity != Verbosity.MINIMAL:
            self.console.print(" | ".join(info_parts))

        if self.verbosity == Verbosity.VERBOSE and message.usage:
            self.console.print(f"[dim]Usage: {self._format_json(message.usage)}[/dim]")

        if message.result and self.verbosity != Verbosity.MINIMAL:
            result_text = message.result
            if len(result_text) > 500 and self.verbosity == Verbosity.MEDIUM:
                result_text = result_text[:500] + "..."
            self.console.print(f"[dim]Result: {escape(result_text)}[/dim]")

    def _format_json(self, data: dict[str, Any]) -> str:
        """Format a dictionary as indented JSON string."""
        try:
            return json.dumps(data, indent=2, default=str)
        except (TypeError, ValueError):
            return str(data)


def create_printer(verbosity: str = "medium") -> MessagePrinter:
    """Create a MessagePrinter with the specified verbosity level.

    Args:
        verbosity: One of "minimal", "medium", or "verbose".

    Returns:
        A configured MessagePrinter instance.
    """
    verbosity_map = {
        "minimal": Verbosity.MINIMAL,
        "medium": Verbosity.MEDIUM,
        "verbose": Verbosity.VERBOSE,
    }
    level = verbosity_map.get(verbosity.lower(), Verbosity.MEDIUM)
    return MessagePrinter(verbosity=level)
