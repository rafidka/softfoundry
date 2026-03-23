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
        self._programmatic_scroll = False

    def add_block(self, widget: Widget) -> None:
        """Append a message widget to the stream.

        Args:
            widget: The widget to append. Auto-scrolls if enabled.
        """
        self.mount(widget)
        if self._auto_scroll:
            self.call_after_refresh(self._scroll_to_bottom_internal)

    def _scroll_to_bottom_internal(self) -> None:
        """Scroll to the bottom without changing auto-scroll state."""
        self._programmatic_scroll = True
        try:
            self.scroll_end(animate=False)
        finally:
            self._programmatic_scroll = False

    def scroll_to_bottom(self) -> None:
        """Force scroll to bottom and re-enable auto-scroll."""
        self._auto_scroll = True
        self._scroll_to_bottom_internal()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Track manual scrolling to toggle auto-scroll behavior."""
        super().watch_scroll_y(old_value, new_value)

        if self._programmatic_scroll:
            return

        if self._is_at_bottom():
            self._auto_scroll = True
            return

        if new_value < old_value:
            self._auto_scroll = False

    def _is_at_bottom(self) -> bool:
        """Check if the scroll position is at (or near) the bottom."""
        max_y = max(0, self.virtual_size.height - self.size.height)
        return self.scroll_offset.y >= (max_y - 2)
