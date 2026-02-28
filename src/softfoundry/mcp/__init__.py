"""MCP orchestration package for softfoundry.

This package provides an MCP server that all agents use for GitHub coordination.
It offers structured access to epic/sub-issue state, PR status, and activity logging.
"""

from softfoundry.mcp.orchestrator import create_orchestrator_server

__all__ = ["create_orchestrator_server"]
