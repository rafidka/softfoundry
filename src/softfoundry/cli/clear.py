"""CLI command to clear sessions and status files."""

import argparse

from softfoundry.utils.sessions import SESSIONS_DIR
from softfoundry.utils.status import STATUS_DIR


def clear_all(dry_run: bool = False) -> None:
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

    # Clear status files
    if STATUS_DIR.exists():
        project_dirs = [d for d in STATUS_DIR.iterdir() if d.is_dir()]
        if project_dirs:
            for project_dir in project_dirs:
                status_files = list(project_dir.glob("*.status"))
                if status_files:
                    print(
                        f"{prefix}Clearing {len(status_files)} status file(s) from {project_dir}"
                    )
                    for f in status_files:
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

    if not dry_run:
        print("\nAll sessions and status files cleared!")


def clear_project(project: str, dry_run: bool = False) -> None:
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

    # Clear status files for this project
    project_status_dir = STATUS_DIR / project
    if project_status_dir.exists():
        status_files = list(project_status_dir.glob("*.status"))
        if status_files:
            print(
                f"{prefix}Clearing {len(status_files)} status file(s) for project '{project}'"
            )
            for f in status_files:
                print(f"  {prefix}Removing: {f.name}")
                if not dry_run:
                    f.unlink()
            # Remove empty project directory
            if not dry_run and not any(project_status_dir.iterdir()):
                print(f"  {prefix}Removing empty directory: {project_status_dir.name}")
                project_status_dir.rmdir()
        else:
            print(f"No status files found for project '{project}'")
    else:
        print(f"No status directory found for project '{project}'")

    if not dry_run:
        print(f"\nAll sessions and status files for '{project}' cleared!")


def main() -> None:
    """Entry point for the clear command."""
    parser = argparse.ArgumentParser(
        description="Clear softfoundry sessions and status files."
    )
    parser.add_argument(
        "--project",
        type=str,
        help="Clear only files for a specific project (default: clear all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )

    args = parser.parse_args()

    if args.project:
        clear_project(args.project, dry_run=args.dry_run)
    else:
        clear_all(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
