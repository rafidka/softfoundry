"""Scrollable message stream container with auto-scroll behavior.

Auto-scrolls to bottom on new messages unless the user has scrolled up.
Pressing End jumps back to bottom and re-enables auto-scroll.
"""

from typing import Any

from textual.containers import VerticalScroll
from textual.widget import Widget


class MessageStream(VerticalScroll):
    """Scrollable container for message widgets.

    Behavior:
    - Auto-scrolls to bottom when new messages are added
    - Stops auto-scrolling when user scrolls up manually
    - Re-enables auto-scroll when user scrolls back to bottom or presses End
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._auto_scroll = True

    def add_block(self, widget: Widget) -> None:
        """Append a message widget to the stream.

        Args:
            widget: The widget to append. Auto-scrolls if enabled.
        """
        self.mount(widget)
        if self._auto_scroll:
            self.call_after_refresh(self._scroll_to_end)

    def _scroll_to_end(self) -> None:
        """Scroll to the very end of the container."""
        self.scroll_end(animate=False)

    def scroll_to_bottom(self) -> None:
        """Force scroll to bottom and re-enable auto-scroll."""
        self._auto_scroll = True
        self.scroll_end(animate=False)

    def on_scroll_up(self) -> None:
        """User scrolled up — disable auto-scroll."""
        if not self._is_at_bottom():
            self._auto_scroll = False

    def on_scroll_down(self) -> None:
        """User scrolled down — re-enable auto-scroll if at bottom."""
        if self._is_at_bottom():
            self._auto_scroll = True

    def _is_at_bottom(self) -> bool:
        """Check if the scroll position is at (or near) the bottom."""
        # Use virtual_size and scroll_offset for Textual's scroll model
        max_y = self.virtual_size.height - self.size.height
        return self.scroll_offset.y >= (max_y - 2)
