"""Sidebar widget with agent status, session info, task, epic progress, and keybindings.

Auto-hides when terminal width < 100 columns.
Toggleable with Ctrl+B.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static


# ─── Data Models ─────────────────────────────────────────────────────────────


@dataclass
class SessionInfo:
    """Session statistics displayed in the sidebar."""

    started_at: float = 0.0  # time.time()
    turns: int = 0
    cost_usd: float = 0.0


@dataclass
class TaskInfo:
    """Current task information."""

    issue_number: int | None = None
    title: str = ""
    pr_number: int | None = None


@dataclass
class EpicIssue:
    """A sub-issue in the epic."""

    number: int
    title: str
    status: str  # "done", "active", "pending", "blocked"


# ─── Status Section ──────────────────────────────────────────────────────────


STATUS_DISPLAY = {
    "idle": ("●", "dim", "Idle"),
    "starting": ("●", "dim", "Starting"),
    "working": ("●", "green", "Working"),
    "thinking": ("●", "magenta", "Thinking"),
    "waiting": ("●", "cyan", "Waiting for answer"),
    "interrupting": ("●", "yellow", "Interrupting"),
    "error": ("●", "red", "Error"),
}


class AgentStatusSection(Static):
    """Colored dot + status text."""

    status: reactive[str] = reactive("idle")

    def render(self) -> str:
        dot, color, label = STATUS_DISPLAY.get(
            self.status, ("●", "dim", self.status.capitalize())
        )
        return f"[{color}]{dot}[/{color}] {label}"


# ─── Session Section ─────────────────────────────────────────────────────────


class SessionSection(Widget):
    """Session duration, turns, cost."""

    turns: reactive[int] = reactive(0)
    cost_usd: reactive[float] = reactive(0.0)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._started_at: float = time.time()
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(1.0, self._refresh_duration)

    def _refresh_duration(self) -> None:
        """Refresh the duration display every second."""
        try:
            duration_label = self.query_one("#session-duration", Static)
            elapsed = time.time() - self._started_at
            duration_label.update(f"  Duration: {self._format_duration(elapsed)}")
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield Static("[bold dim]Session[/bold dim]", classes="sidebar-title")
        yield Static("  Duration: 0s", id="session-duration", classes="session-value")
        yield Static("  Turns: 0", id="session-turns", classes="session-value")
        yield Static("  Cost: $0.0000", id="session-cost", classes="session-value")

    def watch_turns(self, turns: int) -> None:
        try:
            label = self.query_one("#session-turns", Static)
            label.update(f"  Turns: {turns}")
        except Exception:
            pass

    def watch_cost_usd(self, cost_usd: float) -> None:
        try:
            label = self.query_one("#session-cost", Static)
            label.update(f"  Cost: ${cost_usd:.4f}")
        except Exception:
            pass

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds as human-readable duration."""
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        elif s < 3600:
            m, sec = divmod(s, 60)
            return f"{m}m {sec}s"
        else:
            h, rem = divmod(s, 3600)
            m, sec = divmod(rem, 60)
            return f"{h}h {m}m {sec}s"


# ─── Task Section ────────────────────────────────────────────────────────────


class TaskSection(Widget):
    """Current task (issue/PR) being worked on."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_task: TaskInfo = TaskInfo()

    def compose(self) -> ComposeResult:
        yield Static("[bold dim]Task[/bold dim]", classes="sidebar-title")
        yield Static("  No active task", id="task-info", classes="task-info")

    def update_task(self, task: TaskInfo) -> None:
        """Update the displayed task info."""
        self._current_task = task
        try:
            label = self.query_one("#task-info", Static)
            if task.issue_number:
                text = f"  #{task.issue_number} {task.title}"
                if task.pr_number:
                    text += f"\n  PR #{task.pr_number}"
                label.update(text)
            else:
                label.update("  No active task")
        except Exception:
            pass


# ─── Epic Progress Section ───────────────────────────────────────────────────


EPIC_STATUS_ICONS = {
    "done": ("[green]✓[/green]", "epic-done"),
    "active": ("[yellow]●[/yellow]", "epic-active"),
    "pending": ("[dim]○[/dim]", "epic-pending"),
    "blocked": ("[red]✗[/red]", "epic-blocked"),
}


class EpicProgressSection(Widget):
    """Overview of epic sub-issues and their statuses."""

    def __init__(self, epic_number: int | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._epic_number = epic_number
        self._issues: list[EpicIssue] = []

    def compose(self) -> ComposeResult:
        title = "[bold dim]Epic[/bold dim]"
        if self._epic_number:
            title += f" [dim]#{self._epic_number}[/dim]"
        yield Static(title, classes="sidebar-title")
        yield Vertical(id="epic-issues")

    def update_issues(self, issues: list[EpicIssue]) -> None:
        """Update the list of epic sub-issues."""
        self._issues = issues
        try:
            container = self.query_one("#epic-issues", Vertical)
            container.remove_children()
            for issue in issues:
                icon, css_class = EPIC_STATUS_ICONS.get(
                    issue.status, ("[dim]?[/dim]", "epic-pending")
                )
                # Truncate title if too long for sidebar
                title = issue.title
                if len(title) > 22:
                    title = title[:19] + "..."
                container.mount(
                    Static(
                        f"  {icon} #{issue.number} {title}",
                        classes=f"epic-issue {css_class}",
                    )
                )
        except Exception:
            pass


# ─── Key Bindings Section ───────────────────────────────────────────────────


class KeyBindingsSection(Static):
    """Static list of keyboard shortcuts."""

    def render(self) -> str:
        bindings = [
            ("Ctrl+B", "Sidebar"),
            ("Ctrl+D", "Exit"),
            ("Ctrl+J", "Newline"),
            ("End", "Jump to bottom"),
            ("Enter", "Send"),
            ("PgUp/Dn", "Scroll"),
        ]
        lines = ["[bold dim]Keys[/bold dim]"]
        for key, desc in bindings:
            lines.append(f"  [bold]{key:<10}[/bold] [dim]{desc}[/dim]")
        return "\n".join(lines)


# ─── Sidebar Container ──────────────────────────────────────────────────────


class Sidebar(VerticalScroll):
    """Sidebar panel with all information sections.

    Contains:
    - Agent status (colored dot + text)
    - Session info (duration, turns, cost)
    - Current task (issue/PR)
    - Epic progress (sub-issue list)
    - Key bindings reference
    """

    status: reactive[str] = reactive("idle")

    def __init__(
        self,
        agent_type: str = "",
        project: str = "",
        epic_number: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._agent_type = agent_type
        self._project = project
        self._epic_number = epic_number

    def compose(self) -> ComposeResult:
        yield AgentStatusSection(id="sidebar-status")
        yield Static("", classes="sidebar-separator")
        yield SessionSection(id="sidebar-session")
        yield Static("", classes="sidebar-separator")
        yield TaskSection(id="sidebar-task")
        yield Static("", classes="sidebar-separator")
        yield EpicProgressSection(epic_number=self._epic_number, id="sidebar-epic")
        yield Static("", classes="sidebar-separator")
        yield KeyBindingsSection(id="sidebar-keys")

    def watch_status(self, status: str) -> None:
        try:
            status_section = self.query_one("#sidebar-status", AgentStatusSection)
            status_section.status = status
        except Exception:
            pass

    def update_session(self, turns: int, cost_usd: float) -> None:
        """Update session stats."""
        try:
            session = self.query_one("#sidebar-session", SessionSection)
            session.turns = turns
            session.cost_usd = cost_usd
        except Exception:
            pass

    def update_task(self, task: TaskInfo) -> None:
        """Update current task info."""
        try:
            task_section = self.query_one("#sidebar-task", TaskSection)
            task_section.update_task(task)
        except Exception:
            pass

    def update_epic(self, issues: list[EpicIssue]) -> None:
        """Update epic progress."""
        try:
            epic = self.query_one("#sidebar-epic", EpicProgressSection)
            epic.update_issues(issues)
        except Exception:
            pass
