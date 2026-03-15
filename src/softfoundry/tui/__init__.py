"""Textual TUI for softfoundry agents.

Public API:
- SoftFoundryApp: The main Textual application

Usage:
    The Agent base class creates a SoftFoundryApp and runs the agent
    loop as a Textual worker inside the app.
"""

from softfoundry.tui.app import SoftFoundryApp
from softfoundry.tui.widgets.sidebar import EpicIssue, TaskInfo

__all__ = [
    "EpicIssue",
    "SoftFoundryApp",
    "TaskInfo",
]
