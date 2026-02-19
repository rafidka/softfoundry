"""Interactive TUI input handler with prompt_toolkit.

This module provides a minimal async input handler that:
- Reads user input asynchronously
- Calls a callback when input is received
- Displays a status indicator in the prompt
- Can be enabled/disabled by the owner
"""

import asyncio
from collections.abc import Callable
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.patch_stdout import patch_stdout


class InteractiveInput:
    """Async input handler with callback notification.

    This is a minimal input handler that:
    - Reads input from the user asynchronously
    - Calls the provided callback when input is submitted
    - Displays a configurable status in the prompt
    - Can be enabled/disabled (shows a message when disabled)

    The owner (e.g., an agent loop) is responsible for:
    - Setting the status
    - Enabling/disabling input
    - Handling the input in the callback

    Example:
        ```python
        def handle_input(text: str) -> None:
            print(f"Received: {text}")

        interactive = InteractiveInput(on_input=handle_input)

        async def main():
            async with interactive:
                interactive.status = "working"
                await interactive.run()
        ```
    """

    def __init__(
        self,
        on_input: Callable[[str], None],
        prompt: str = "> ",
    ):
        """Initialize the interactive input handler.

        Args:
            on_input: Callback called when user submits input.
                This is called synchronously from the input loop.
            prompt: The prompt string to display after the status.
        """
        self._on_input = on_input
        self._prompt = prompt
        self._session: PromptSession[str] | None = None
        self._running = False
        self._status = "idle"
        self._enabled = True
        self._disabled_message = "Please wait..."
        self._patch_context: Any = None

    @property
    def status(self) -> str:
        """Get the current status displayed in the prompt."""
        return self._status

    @status.setter
    def status(self, value: str) -> None:
        """Set the status displayed in the prompt."""
        self._status = value

    @property
    def enabled(self) -> bool:
        """Check if input is currently enabled."""
        return self._enabled

    def enable(self) -> None:
        """Enable input (allow user to type)."""
        self._enabled = True

    def disable(self, message: str = "Please wait...") -> None:
        """Disable input with a message.

        Args:
            message: Message shown instead of the input prompt.
        """
        self._enabled = False
        self._disabled_message = message

    def _get_prompt(self) -> FormattedText:
        """Generate the prompt with status indicator."""
        status_styles = {
            "idle": "ansibrightblack",
            "waiting": "ansicyan",
            "working": "ansiyellow",
            "thinking": "ansimagenta",
        }
        style = status_styles.get(self._status, "ansibrightblack")
        return FormattedText([(style, f"[{self._status}] "), ("", self._prompt)])

    async def __aenter__(self) -> "InteractiveInput":
        """Enter the async context, setting up stdout patching."""
        self._session = PromptSession()
        self._patch_context = patch_stdout(raw=True)
        self._patch_context.__enter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the async context, cleaning up."""
        self._running = False
        if self._patch_context:
            self._patch_context.__exit__(exc_type, exc_val, exc_tb)
            self._patch_context = None
        self._session = None

    async def run(self) -> None:
        """Run the input loop.

        Continuously reads input from the user and calls the on_input
        callback when input is submitted. When disabled, shows the
        disabled message and waits for re-enable.

        This should be run as a background task.
        """
        if not self._session:
            raise RuntimeError("Must be used within async context manager")

        self._running = True

        while self._running:
            try:
                # If disabled, wait until enabled
                while not self._enabled and self._running:
                    # Show disabled message briefly, then check again
                    print(f"\r[dim]{self._disabled_message}[/dim]", end="", flush=True)
                    await asyncio.sleep(0.1)

                if not self._running:
                    break

                # Read input asynchronously
                user_input = await self._session.prompt_async(self._get_prompt)

                if user_input and user_input.strip():
                    self._on_input(user_input.strip())

            except EOFError:
                # Ctrl+D pressed
                break
            except asyncio.CancelledError:
                break
            except KeyboardInterrupt:
                # Ctrl+C - stop the input loop, let main handle exit
                self._running = False
                raise

    def stop(self) -> None:
        """Stop the input loop."""
        self._running = False
