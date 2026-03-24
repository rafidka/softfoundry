"""Clear command for softfoundry CLI."""

from typing import Annotated

import typer

from softfoundry.utils.env import initialize_environment
from softfoundry.agents.sessions import SESSIONS_DIR
from softfoundry.agents.transcript import LOGS_DIR
from softfoundry.utils.status import STATUS_DIR


def _clear_all(dry_run: bool = False) -> None:
    """Clear all sessions and status files.

    Args:
        dry_run: If True, only print what would be deleted without deleting.
    """
    prefix = "[DRY RUN] " if dry_run else ""

    # Clear sessions
    if SESSIONS_DIR.exists():
        session_files = list(SESSIONS_DIR.glob("*.json"))
        if session_files:
            print(
                f"{prefix}Clearing {len(session_files)} session file(s) from {SESSIONS_DIR}"
            )
            for f in session_files:
                print(f"  {prefix}Removing: {f.name}")
                if not dry_run:
                    f.unlink()
        else:
            print(f"No session files found in {SESSIONS_DIR}")
    else:
        print(f"Sessions directory does not exist: {SESSIONS_DIR}")

    # Clear status and memory files
    if STATUS_DIR.exists():
        project_dirs = [d for d in STATUS_DIR.iterdir() if d.is_dir()]
        if project_dirs:
            for project_dir in project_dirs:
                status_files = list(project_dir.glob("*.status"))
                memory_files = list(project_dir.glob("*.memory.md"))
                agent_files = status_files + memory_files
                if agent_files:
                    print(
                        f"{prefix}Clearing {len(agent_files)} agent file(s) from {project_dir}"
                    )
                    for f in agent_files:
                        print(f"  {prefix}Removing: {f.name}")
                        if not dry_run:
                            f.unlink()
                # Remove empty project directory
                if (
                    not dry_run
                    and project_dir.exists()
                    and not any(project_dir.iterdir())
                ):
                    print(f"  {prefix}Removing empty directory: {project_dir.name}")
                    project_dir.rmdir()
        else:
            print(f"No project directories found in {STATUS_DIR}")
    else:
        print(f"Status directory does not exist: {STATUS_DIR}")

    # Clear log files
    if LOGS_DIR.exists():
        project_dirs = [d for d in LOGS_DIR.iterdir() if d.is_dir()]
        if project_dirs:
            for project_dir in project_dirs:
                log_files = list(project_dir.glob("*.md"))
                if log_files:
                    print(
                        f"{prefix}Clearing {len(log_files)} log file(s) from {project_dir}"
                    )
                    for f in log_files:
                        print(f"  {prefix}Removing: {f.name}")
                        if not dry_run:
                            f.unlink()
                # Remove empty project directory
                if (
                    not dry_run
                    and project_dir.exists()
                    and not any(project_dir.iterdir())
                ):
                    print(f"  {prefix}Removing empty directory: {project_dir.name}")
                    project_dir.rmdir()
        else:
            print(f"No log directories found in {LOGS_DIR}")
    else:
        print(f"Logs directory does not exist: {LOGS_DIR}")

    if not dry_run:
        print("\nAll sessions, status files, memory files, and logs cleared!")


def _clear_project(project: str, dry_run: bool = False) -> None:
    """Clear sessions and status files for a specific project.

    Args:
        project: The project name to clear.
        dry_run: If True, only print what would be deleted without deleting.
    """
    prefix = "[DRY RUN] " if dry_run else ""

    # Clear sessions for this project
    if SESSIONS_DIR.exists():
        session_files = list(SESSIONS_DIR.glob(f"*-{project}.json"))
        if session_files:
            print(
                f"{prefix}Clearing {len(session_files)} session file(s) for project '{project}'"
            )
            for f in session_files:
                print(f"  {prefix}Removing: {f.name}")
                if not dry_run:
                    f.unlink()
        else:
            print(f"No session files found for project '{project}'")

    # Clear status and memory files for this project
    project_status_dir = STATUS_DIR / project
    if project_status_dir.exists():
        status_files = list(project_status_dir.glob("*.status"))
        memory_files = list(project_status_dir.glob("*.memory.md"))
        agent_files = status_files + memory_files
        if agent_files:
            print(
                f"{prefix}Clearing {len(agent_files)} agent file(s) for project '{project}'"
            )
            for f in agent_files:
                print(f"  {prefix}Removing: {f.name}")
                if not dry_run:
                    f.unlink()
            # Remove empty project directory
            if not dry_run and not any(project_status_dir.iterdir()):
                print(f"  {prefix}Removing empty directory: {project_status_dir.name}")
                project_status_dir.rmdir()
        else:
            print(f"No agent files found for project '{project}'")
    else:
        print(f"No status directory found for project '{project}'")

    # Clear log files for this project
    project_logs_dir = LOGS_DIR / project
    if project_logs_dir.exists():
        log_files = list(project_logs_dir.glob("*.md"))
        if log_files:
            print(
                f"{prefix}Clearing {len(log_files)} log file(s) for project '{project}'"
            )
            for f in log_files:
                print(f"  {prefix}Removing: {f.name}")
                if not dry_run:
                    f.unlink()
            # Remove empty project directory
            if not dry_run and not any(project_logs_dir.iterdir()):
                print(f"  {prefix}Removing empty directory: {project_logs_dir.name}")
                project_logs_dir.rmdir()
        else:
            print(f"No log files found for project '{project}'")
    else:
        print(f"No logs directory found for project '{project}'")

    if not dry_run:
        print(
            f"\nAll sessions, status files, memory files, and logs for '{project}' cleared!"
        )


def register_command(app: typer.Typer) -> tuple:
    """Register the clear command with the Typer app."""

    @app.command(help="Clear softfoundry sessions and status files.")
    def clear(
        project: Annotated[
            str | None,
            typer.Option(help="Clear only files for a specific project (default: all)"),
        ] = None,
        dry_run: Annotated[
            bool,
            typer.Option(
                "--dry-run", help="Show what would be deleted without deleting"
            ),
        ] = False,
    ) -> None:
        initialize_environment()

        if project:
            _clear_project(project, dry_run=dry_run)
        else:
            _clear_all(dry_run=dry_run)

    return (clear,)
