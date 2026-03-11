"""Rich text input area with status indicator and dual-mode behavior.

Modes:
- Normal: input sends a user message (or interrupts if agent is busy)
- Question: input answers the pending question from the agent

The mode switches automatically based on whether ask_user is active.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static, TextArea

STATUS_COLORS = {
    "idle": "dim",
    "starting": "dim",
    "working": "yellow",
    "thinking": "magenta",
    "waiting": "cyan",
    "interrupting": "yellow",
    "error": "red",
}


class _SubmitTextArea(TextArea):
    """TextArea that submits on Enter and inserts newline on Ctrl+J.

    Standard TextArea consumes Enter to insert newlines, preventing
    the parent InputArea's binding from ever firing. This subclass
    intercepts key events to reverse that behavior:
    - Enter  -> post Submitted message on parent InputArea
    - Ctrl+J -> insert a newline (the original Enter behavior)
    """

    async def _on_key(self, event: Key) -> None:
        if event.key == "enter":
            # Prevent TextArea from inserting a newline
            event.prevent_default()
            event.stop()
            # Delegate submission to the parent InputArea
            for ancestor in self.ancestors_with_self:
                if isinstance(ancestor, InputArea):
                    ancestor.action_submit()
                    return
        elif event.key == "ctrl+j":
            # Insert a newline (mimic the original Enter behavior)
            event.prevent_default()
            event.stop()
            self.insert("\n")


class InputArea(Widget):
    """Composite input widget with status indicator and text area.

    Layout:
        [status] context_label > (text area)

    Dual-mode:
    - Normal: label shows "> " — input will be sent as user message
    - Question: label shows "Answer > " with cyan styling
    """

    status: reactive[str] = reactive("idle")
    question_mode: reactive[bool] = reactive(False)
    disabled_mode: reactive[bool] = reactive(False)

    class Submitted(Message):
        """Posted when the user submits input."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._disabled_message = "Please wait..."

    def compose(self) -> ComposeResult:
        with Vertical(id="input-container"):
            # yield Static(id="input-label")
            yield _SubmitTextArea(
                id="input-area", language=None, show_line_numbers=False
            )
            yield Static(id="input-disabled-label")

    def on_mount(self) -> None:
        # self._update_label()
        self._update_disabled_state()
        # Focus the text area
        text_area = self.query_one("#input-area", _SubmitTextArea)
        text_area.focus()

    def watch_status(self, status: str) -> None:
        # self._update_label()
        pass

    def watch_question_mode(self, question_mode: bool) -> None:
        # self._update_label()
        pass

    def watch_disabled_mode(self, disabled_mode: bool) -> None:
        self._update_disabled_state()

    # def _update_label(self) -> None:
    #     """Update the input label based on current mode and status."""
    #     try:
    #         label = self.query_one("#input-label", Static)
    #     except Exception:
    #         return

    #     color = STATUS_COLORS.get(self.status, "dim")

    #     if self.question_mode:
    #         label.update(f"[{color}]\\[{self.status}][/{color}] [cyan]Answer >[/cyan] ")
    #         label.add_class("waiting")
    #     else:
    #         label.update(f"[{color}]\\[{self.status}][/{color}] > ")
    #         label.remove_class("waiting")

    def _update_disabled_state(self) -> None:
        """Show/hide text area vs disabled label."""
        try:
            text_area = self.query_one("#input-area", _SubmitTextArea)
            disabled_label = self.query_one("#input-disabled-label", Static)
        except Exception:
            return

        if self.disabled_mode:
            text_area.display = False
            disabled_label.display = True
            disabled_label.update(f"[dim italic]{self._disabled_message}[/dim italic]")
        else:
            text_area.display = True
            disabled_label.display = False
            text_area.focus()

    def enable(self) -> None:
        """Enable input (allow user to type)."""
        self.disabled_mode = False

    def disable(self, message: str = "Please wait...") -> None:
        """Disable input with a message."""
        self._disabled_message = message
        self.disabled_mode = True

    def action_submit(self) -> None:
        """Handle Enter key — submit the input."""
        try:
            text_area = self.query_one("#input-area", _SubmitTextArea)
        except Exception:
            return

        text = text_area.text.strip()
        if text:
            self.post_message(self.Submitted(text))
            text_area.clear()

    def focus_input(self) -> None:
        """Focus the text area."""
        if not self.disabled_mode:
            try:
                text_area = self.query_one("#input-area", _SubmitTextArea)
                text_area.focus()
            except Exception:
                pass
