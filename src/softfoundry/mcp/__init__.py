"""MCP orchestration package for softfoundry.

This package provides MCP servers for agent coordination:
- orchestrator: GitHub-based coordination (issues, PRs, labels, activity)
- user: User interaction tools (ask_user, ask_user_choice)
"""

from softfoundry.mcp.orchestrator import create_orchestrator_server
from softfoundry.mcp.user_server import UserInputServer, create_user_server

__all__ = ["UserInputServer", "create_orchestrator_server", "create_user_server"]
