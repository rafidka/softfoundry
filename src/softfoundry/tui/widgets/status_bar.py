"""Bottom status bar widget showing at-a-glance agent info."""

from typing import Any

from textual.reactive import reactive
from textual.widgets import Static


STATUS_DOTS = {
    "idle": ("●", "dim"),
    "starting": ("●", "dim"),
    "working": ("●", "green"),
    "thinking": ("●", "magenta"),
    "waiting": ("●", "cyan"),
    "interrupting": ("●", "yellow"),
    "error": ("●", "red"),
}


class StatusBar(Static):
    """Single-line footer bar with agent status summary.

    Displays: status dot, status text, turn count, cost, agent info.

    Example:
        ● Working · Turns: 5 · Cost: $0.0234 · softfoundry/programmer
    """

    status: reactive[str] = reactive("idle")
    turns: reactive[int] = reactive(0)
    cost_usd: reactive[float] = reactive(0.0)
    agent_label: reactive[str] = reactive("")

    def __init__(
        self,
        agent_type: str = "",
        project: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.id = "status-bar"
        if project and agent_type:
            self.agent_label = f"{project}/{agent_type}"
        elif agent_type:
            self.agent_label = agent_type

    def render(self) -> str:
        dot, color = STATUS_DOTS.get(self.status, ("●", "dim"))
        parts = [
            f"[{color}]{dot}[/{color}] {self.status.capitalize()}",
        ]
        if self.turns > 0:
            parts.append(f"Turns: {self.turns}")
        if self.cost_usd > 0:
            parts.append(f"Cost: ${self.cost_usd:.4f}")
        if self.agent_label:
            parts.append(self.agent_label)
        return " · ".join(parts)
