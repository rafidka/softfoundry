"""Logs command for softfoundry CLI — list and view session transcripts."""

import os
from datetime import datetime
from typing import Annotated

import typer

from softfoundry.agents.transcript import LOGS_DIR
from softfoundry.utils.env import initialize_environment


def _collect_logs(project: str | None = None) -> list[dict]:
    """Collect log files, sorted by modification time (newest first).

    Args:
        project: Optional project name to filter by.

    Returns:
        List of dicts with keys: path, project, agent, date, size.
    """
    if not LOGS_DIR.exists():
        return []

    if project:
        search_dirs = [LOGS_DIR / project]
    else:
        search_dirs = [d for d in LOGS_DIR.iterdir() if d.is_dir()]

    logs = []
    for dir_path in search_dirs:
        if not dir_path.exists():
            continue
        for log_file in dir_path.glob("*.md"):
            stat = log_file.stat()
            # Parse agent info from filename: {agent_type}-{name}-{timestamp}.md
            stem = log_file.stem
            # Timestamp is always the last 15 chars: YYYYMMDD-HHMMSS
            if len(stem) > 16 and stem[-15:-6].isdigit():
                timestamp_str = stem[-15:]
                agent_part = stem[: -(len(timestamp_str) + 1)]
            else:
                timestamp_str = ""
                agent_part = stem

            try:
                date = datetime.strptime(timestamp_str, "%Y%m%d-%H%M%S")
                date_str = date.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                date_str = datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            # Format file size
            size_bytes = stat.st_size
            if size_bytes < 1024:
                size_str = f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"

            logs.append(
                {
                    "path": log_file,
                    "project": dir_path.name,
                    "agent": agent_part,
                    "date": date_str,
                    "size": size_str,
                    "mtime": stat.st_mtime,
                }
            )

    logs.sort(key=lambda x: x["mtime"], reverse=True)
    return logs


def _print_log_table(logs: list[dict]) -> None:
    """Print a formatted table of log files.

    Args:
        logs: List of log info dicts.
    """
    if not logs:
        print("No session logs found.")
        return

    # Calculate column widths
    headers = ["#", "Agent", "Project", "Date", "Size"]
    idx_width = len(str(len(logs)))
    agent_width = max(len(l["agent"]) for l in logs)
    project_width = max(len(l["project"]) for l in logs)

    agent_width = max(agent_width, len("Agent"))
    project_width = max(project_width, len("Project"))

    fmt = f"{{:<{max(idx_width, 1)}}}  {{:<{agent_width}}}  {{:<{project_width}}}  {{:<19}}  {{}}"

    print(fmt.format(*headers))
    print(
        fmt.format(
            "-" * max(idx_width, 1),
            "-" * agent_width,
            "-" * project_width,
            "-" * 19,
            "-" * 8,
        )
    )

    for i, log in enumerate(logs, 1):
        print(fmt.format(i, log["agent"], log["project"], log["date"], log["size"]))


def _view_log(log_path: os.PathLike) -> None:
    """Print a log file's contents to stdout.

    Args:
        log_path: Path to the log file.
    """
    with open(log_path, encoding="utf-8") as f:
        print(f.read())


def register_command(app: typer.Typer) -> tuple:
    """Register the logs command with the Typer app."""

    @app.command(help="List and view session transcript logs.")
    def logs(
        project: Annotated[
            str | None,
            typer.Option(help="Filter logs by project name"),
        ] = None,
        view: Annotated[
            int | None,
            typer.Option("--view", "-v", help="View log by index number"),
        ] = None,
        last: Annotated[
            bool,
            typer.Option("--last", help="View the most recent log"),
        ] = False,
    ) -> None:
        initialize_environment()

        all_logs = _collect_logs(project)

        if last:
            if not all_logs:
                print("No session logs found.")
                raise typer.Exit(1)
            _view_log(all_logs[0]["path"])
        elif view is not None:
            if not all_logs:
                print("No session logs found.")
                raise typer.Exit(1)
            if view < 1 or view > len(all_logs):
                print(f"Invalid index: {view}. Valid range: 1-{len(all_logs)}")
                raise typer.Exit(1)
            _view_log(all_logs[view - 1]["path"])
        else:
            _print_log_table(all_logs)

    return (logs,)
