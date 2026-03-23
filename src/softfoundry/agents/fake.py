"""Fake agent session for testing the Textual TUI without GitHub mutations.

This module runs a deterministic scripted session using the same `SoftFoundryApp`
used by real agents. It emits assistant text, thinking blocks, tool blocks, and
interactive questions that wait for user responses.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from softfoundry.tui import EpicIssue, SoftFoundryApp


class FakeSessionRunner:
    """Drive a fake scripted session inside the real TUI."""

    def __init__(self, app: SoftFoundryApp, step_delay: float = 0.6) -> None:
        self._app = app
        self._step_delay = step_delay
        self._response_event = asyncio.Event()
        self._pending_response: str | None = None
        self._waiting_for_question = False

    def on_input(self, text: str) -> None:
        """Handle user input from the TUI.

        Args:
            text: Input submitted by the user.
        """
        if self._waiting_for_question:
            self._pending_response = text
            self._response_event.set()
            return

        self._app.add_lifecycle_message(
            "Input received. A question will appear shortly.",
            "info",
        )

    async def run(self) -> None:
        """Run a fake end-to-end interaction sequence."""
        self._app.update_status("starting")
        self._app.add_lifecycle_message(
            "Starting fake agent session (no GitHub/API calls).",
            "info",
        )
        self._app.add_system_block("fake_session_boot")
        self._app.update_task_info(
            issue_number=9, title="Fake TUI session", pr_number=0
        )
        self._app.update_epic_progress(
            [
                EpicIssue(number=1, title="Boot fake session", status="active"),
                EpicIssue(number=2, title="Show all widgets", status="pending"),
                EpicIssue(number=3, title="Collect test feedback", status="pending"),
            ]
        )
        await self._sleep_step()

        self._app.update_status("thinking")
        self._app.add_text_message(
            "Hello! I am a fake agent session used for TUI testing."
        )
        self._app.add_text_message(
            "Here is a copyable code sample:\n\n"
            "```python\n"
            "def greet(name: str) -> str:\n"
            '    return f"Hello, {name}!"\n'
            "\n"
            'print(greet("softfoundry"))\n'
            "```\n"
            "\n"
            "And a shell snippet:\n\n"
            "```bash\n"
            "uv run sf fake --verbosity verbose --step-delay 0.2\n"
            "```"
        )
        self._app.add_thinking_block(
            "I should demonstrate the same UX surfaces as a real session: "
            "messages, tools, questions, and completion states."
        )
        await self._sleep_step()

        self._app.update_status("working")
        tool_id = f"fake-tool-{uuid4().hex[:8]}"
        self._app.add_tool_block(
            tool_use_id=tool_id,
            tool_name="Bash",
            tool_input={
                "description": "Simulated setup",
                "command": "uv run sf manager --help",
            },
        )
        await self._sleep_step()
        self._app.update_tool_result(
            tool_use_id=tool_id,
            content=(
                "This is a fake tool result. No command was executed.\n"
                "Use this flow to verify tool block rendering and collapse behavior."
            ),
            is_error=False,
        )
        await self._sleep_step()

        error_tool_id = f"fake-tool-{uuid4().hex[:8]}"
        self._app.add_tool_block(
            tool_use_id=error_tool_id,
            tool_name="Grep",
            tool_input={
                "pattern": "fake|session",
                "include": "*.py",
            },
        )
        await self._sleep_step()
        self._app.update_tool_result(
            tool_use_id=error_tool_id,
            content="Error: simulated tool failure for error-state rendering test.",
            is_error=True,
        )

        self._app.add_session_separator("Phase 2: Interactive prompts")
        self._app.update_epic_progress(
            [
                EpicIssue(number=1, title="Boot fake session", status="done"),
                EpicIssue(number=2, title="Show all widgets", status="active"),
                EpicIssue(number=3, title="Collect test feedback", status="pending"),
            ]
        )

        answer = await self._ask_question(
            "Which part of the TUI do you want to test next?\n\n"
            "You can also copy this inline example from a question block:\n"
            "`uv run sf fake --step-delay 0.1`",
            options=[
                "Input handling",
                "Tool blocks",
                "Status transitions",
            ],
        )

        self._app.update_status("working")
        self._app.add_text_message(f"Great choice: **{answer}**.")
        await self._sleep_step()

        free_text = await self._ask_question(
            "Share one thing that looked off in this session (or type 'none').\n\n"
            "Optional code-style reply template:\n"
            '`copy_issue = "selection not captured in code fence"`'
        )

        self._app.update_status("working")
        self._app.add_text_message(f"Captured feedback: `{free_text}`")
        self._app.add_lifecycle_message(
            "Generating extra messages to test scrolling behavior...",
            "info",
        )
        await self._emit_scroll_test_messages()
        self._app.update_epic_progress(
            [
                EpicIssue(number=1, title="Boot fake session", status="done"),
                EpicIssue(number=2, title="Show all widgets", status="done"),
                EpicIssue(number=3, title="Collect test feedback", status="active"),
            ]
        )
        await self._sleep_step()

        self._app.add_result_block(
            is_error=False,
            subtype="fake_session_complete",
            duration_ms=None,
            cost_usd=0.0,
            num_turns=1,
        )
        self._app.update_session_stats(turns=1, cost_usd=0.0)
        self._app.update_epic_progress(
            [
                EpicIssue(number=1, title="Boot fake session", status="done"),
                EpicIssue(number=2, title="Show all widgets", status="done"),
                EpicIssue(number=3, title="Collect test feedback", status="done"),
            ]
        )
        self._app.update_status("idle")
        self._app.add_lifecycle_message("Fake session completed.", "success")
        self._app.add_lifecycle_message("Press Ctrl+D to exit.", "info")
        self._app.enable_input()

    async def _ask_question(
        self,
        question: str,
        options: list[str] | None = None,
    ) -> str:
        """Show a question and wait for a response from the user."""
        self._waiting_for_question = True
        self._pending_response = None
        self._response_event.clear()
        self._app.update_status("waiting")
        self._app.enable_input()
        self._app.add_question_block(question, options)

        await self._response_event.wait()

        response = (self._pending_response or "").strip()
        if options:
            try:
                idx = int(response) - 1
                if 0 <= idx < len(options):
                    response = options[idx]
            except ValueError:
                pass

        self._waiting_for_question = False
        self._pending_response = None
        self._app.clear_question_mode()
        return response

    async def _sleep_step(self) -> None:
        """Apply a configurable pacing delay between UI events."""
        if self._step_delay > 0:
            await asyncio.sleep(self._step_delay)

    async def _emit_scroll_test_messages(self) -> None:
        """Emit a larger stream of messages for scroll testing."""
        for i in range(1, 15):
            self._app.add_text_message(
                f"Scroll test message {i}/14: quick line for viewport overflow checks."
            )

            if i in (4, 9, 13):
                self._app.add_text_message(
                    "```python\n"
                    f"def scroll_probe_{i}(value: int) -> int:\n"
                    "    return value * 2\n"
                    "```"
                )

            if i % 5 == 0:
                self._app.add_lifecycle_message(
                    f"Checkpoint {i}/14 reached.",
                    "info",
                )

            await self._sleep_step()


async def run_fake_session(verbosity: str = "medium", step_delay: float = 0.6) -> None:
    """Run a fake agent session in the same TUI used by real agents.

    Args:
        verbosity: TUI verbosity level (minimal, medium, verbose).
        step_delay: Delay in seconds between scripted events.
    """
    app: SoftFoundryApp | None = None
    runner: FakeSessionRunner | None = None

    async def _worker() -> None:
        assert runner is not None
        await runner.run()

    app = SoftFoundryApp(
        agent_type="fake",
        agent_name="session",
        project="softfoundry",
        on_input=lambda _text: None,
        agent_coroutine=_worker,
    )
    app.verbosity = verbosity

    runner = FakeSessionRunner(app=app, step_delay=step_delay)
    app._on_input = runner.on_input

    await app.run_async()
