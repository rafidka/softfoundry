"""MCP server for user interaction tools.

This module provides an in-process MCP server with tools that let Claude
explicitly request user input. When Claude calls ask_user or ask_user_choice,
the tool handler displays the question in the TUI, enters question mode,
and awaits an asyncio.Event until the user responds.

This replaces the old LLM-based question detection (needs_user_input) with
an explicit tool-based approach — Claude decides when to ask, not a classifier.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from softfoundry.tui.app import SoftFoundryApp


def _success(data: str) -> dict[str, Any]:
    """Create a success response."""
    return {"content": [{"type": "text", "text": data}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {
        "content": [{"type": "text", "text": f"Error: {message}"}],
        "isError": True,
    }


class UserInputServer:
    """Server-side state for ask_user MCP tools.

    Holds an asyncio.Event that tool handlers await, and a response
    slot that the agent loop fills when the user submits input via the TUI.

    Lifecycle:
        1. Created by create_user_server() before the TUI starts
        2. App is set via set_app() after TUI initialization
        3. Tool handlers call _wait_for_input() which blocks until user responds
        4. Agent loop calls provide_response() when user submits during question mode
    """

    def __init__(self) -> None:
        self._app: SoftFoundryApp | None = None
        self._event: asyncio.Event | None = None
        self._response: str | None = None
        self._waiting: bool = False

    def set_app(self, app: SoftFoundryApp) -> None:
        """Set the TUI app for displaying questions.

        Args:
            app: The SoftFoundryApp instance.
        """
        self._app = app

    @property
    def is_waiting(self) -> bool:
        """True if an ask_user tool is currently waiting for user input."""
        return self._waiting

    def provide_response(self, text: str) -> None:
        """Provide the user's response to a pending ask_user call.

        Called by the agent loop when the user submits input while
        a tool is waiting in question mode.

        Args:
            text: The user's response text.
        """
        self._response = text
        if self._event:
            self._event.set()

    async def _wait_for_input(
        self, question: str, options: list[str] | None = None
    ) -> str:
        """Display a question in the TUI and wait for user response.

        Args:
            question: The question to display.
            options: Optional list of choices for ask_user_choice.

        Returns:
            The user's response text.
        """
        self._event = asyncio.Event()
        self._response = None
        self._waiting = True

        # Display question in TUI
        if self._app:
            self._app.add_question_block(question, options)

        try:
            await self._event.wait()
            return self._response or ""
        finally:
            self._waiting = False
            self._event = None
            if self._app:
                self._app.clear_question_mode()


def create_user_server(name: str = "user") -> tuple[Any, UserInputServer]:
    """Create the user input MCP server.

    Returns a tuple of (server_config, server_state):
    - server_config goes into AgentConfig.mcp_servers
    - server_state is kept by the agent for calling provide_response()

    Args:
        name: The MCP server name (tools will be mcp__{name}__ask_user, etc.)

    Returns:
        Tuple of (McpSdkServerConfig, UserInputServer).
    """
    server = UserInputServer()

    @tool(
        "ask_user",
        "Ask the user a question and wait for their free-text response. "
        "Use this when you need information, clarification, or a decision from the user.",
        {"question": str},
    )
    async def tool_ask_user(args: dict[str, Any]) -> dict[str, Any]:
        question = args.get("question", "")
        if not question:
            return _error("question is required")
        response = await server._wait_for_input(question)
        return _success(response)

    @tool(
        "ask_user_choice",
        "Ask the user to choose from a list of options. "
        "The user can select by number or type a custom answer. "
        "Use this when there are specific choices to present.",
        {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of options for the user to choose from.",
                    "minItems": 2,
                },
            },
            "required": ["question", "options"],
        },
    )
    async def tool_ask_user_choice(args: dict[str, Any]) -> dict[str, Any]:
        question = args.get("question", "")
        options = args.get("options", [])
        if not question:
            return _error("question is required")
        if not options or len(options) < 2:
            return _error("At least 2 options are required")

        response = await server._wait_for_input(question, options)

        # If user typed a number, map to the corresponding option
        try:
            idx = int(response.strip()) - 1
            if 0 <= idx < len(options):
                return _success(options[idx])
        except ValueError:
            pass

        # Return the free-text answer as-is
        return _success(response)

    mcp_config = create_sdk_mcp_server(
        name=name,
        version="1.0.0",
        tools=[tool_ask_user, tool_ask_user_choice],
    )

    return mcp_config, server
