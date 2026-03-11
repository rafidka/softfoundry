"""Textual TUI for softfoundry agents.

Public API:
- SoftFoundryApp: The main Textual application
- AgentBridge: Adapter between Agent base class and the TUI

Usage:
    The Agent base class creates a SoftFoundryApp and AgentBridge,
    then runs the agent loop as a Textual worker inside the app.
"""

from softfoundry.tui.app import SoftFoundryApp
from softfoundry.tui.bridge import AgentBridge
from softfoundry.tui.widgets.sidebar import EpicIssue, TaskInfo

__all__ = [
    "AgentBridge",
    "EpicIssue",
    "SoftFoundryApp",
    "TaskInfo",
]
