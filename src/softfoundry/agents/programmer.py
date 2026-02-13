"""Programmer agent that picks up and works on assigned tasks."""

import argparse
import asyncio
import signal
import sys

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from softfoundry.utils.output import create_printer
from softfoundry.utils.sessions import SessionManager, format_session_info
from softfoundry.utils.state import (
    ProgrammerState,
    get_assigned_task,
    get_programmer_state,
    sanitize_filename,
)

AGENT_TYPE = "programmer"
POLL_INTERVAL = 30  # seconds to wait when polling for task assignment
DEFAULT_MAX_ITERATIONS = 100  # prevent infinite loops during testing

# System prompt for the programmer agent
SYSTEM_PROMPT = """
You are a programmer named {name}. You work with a group of AI agents to
implement a software project end-to-end.

Your workflow:
1. Register yourself as available in the team directory
2. Wait for the manager to assign you a task
3. Work on your assigned task
4. Mark the task as completed and yourself as available again
5. Repeat

Always update your status in your team file and the task file as you progress.
"""

# State-specific prompts
PROMPTS = {
    "initial": """
Declare yourself available by creating a file at `{planning_dir}/team/{filename}`.
Use the template at `{planning_dir}/team/TEMPLATE.md` as a guide.
Set your Status to AVAILABLE.
""",
    "waiting": """
Check your team file at `{planning_dir}/team/{filename}` to see if you've been
assigned a task. Look at the "Status" and "Assigned Task" fields.

If your Status is ASSIGNED and there's a task filename in "Assigned Task":
1. Read the task file from `{planning_dir}/tasks/`
2. Update your Status to WORKING
3. Begin implementing the task in the project directory: `{project_dir}/`

If your Status is still AVAILABLE, just confirm you're waiting for assignment.
""",
    "working": """
Continue working on your assigned task: `{planning_dir}/tasks/{task_filename}`

The implementation should go in the project directory: `{project_dir}/`

Update the task file's Comments section with your progress.

When the task is complete:
1. Set the task's Status to COMPLETED in the task file
2. Add final notes to the task's Comments section
3. Update your team file: set Status to AVAILABLE, set "Assigned Task" to None
""",
}


class GracefulExit(Exception):
    """Raised when user requests graceful shutdown."""

    pass


class ImmediateExit(Exception):
    """Raised when user requests immediate shutdown."""

    pass


def setup_signal_handlers() -> dict[str, bool]:
    """Set up signal handlers for graceful shutdown.

    Returns:
        A dict with 'shutdown_requested' key to track shutdown state.
    """
    state = {"shutdown_requested": False, "query_running": False}

    def handler(signum: int, frame: object) -> None:
        if state["shutdown_requested"]:
            # Second Ctrl+C - exit immediately
            print("\nImmediate shutdown requested.")
            raise ImmediateExit()
        else:
            state["shutdown_requested"] = True
            if state["query_running"]:
                print("\nShutdown requested. Waiting for current query to complete...")
                print("Press Ctrl+C again to exit immediately.")
            else:
                raise GracefulExit()

    signal.signal(signal.SIGINT, handler)
    return state


async def run_programmer(
    programmer_name: str,
    project_directory: str,
    verbosity: str = "medium",
    resume: bool = False,
    new_session: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> None:
    """Run the programmer agent in a continuous loop.

    Args:
        programmer_name: Name of the programmer agent.
        project_directory: Path to the project directory.
        verbosity: Output verbosity level (minimal, medium, verbose).
        resume: If True, automatically resume existing session (fail if none exists).
        new_session: If True, always start a new session (delete existing if present).
        max_iterations: Maximum loop iterations (safety limit).
    """
    printer = create_printer(verbosity)
    session_manager = SessionManager(project_directory)
    planning_dir = f"{project_directory}-planning"

    # Set up signal handlers for graceful shutdown
    shutdown_state = setup_signal_handlers()

    # Handle session resumption
    existing_session = session_manager.get_session(AGENT_TYPE, programmer_name)
    current_session_id: str | None = None

    if existing_session:
        if new_session:
            session_manager.delete_session(AGENT_TYPE, programmer_name)
            printer.console.print(f"Deleted existing session for {programmer_name}.")
        elif resume:
            current_session_id = existing_session.session_id
            printer.console.print(f"Resuming session for {programmer_name}...")
        else:
            printer.console.print(f"Found previous session for {programmer_name}:")
            print(format_session_info(existing_session))
            response = input("Continue previous session? [y/N]: ").strip().lower()
            if response == "y":
                current_session_id = existing_session.session_id
                printer.console.print("Resuming session...")
            else:
                session_manager.delete_session(AGENT_TYPE, programmer_name)
                printer.console.print("Starting new session...")
    elif resume:
        print(
            f"Error: No existing session found for {programmer_name}.", file=sys.stderr
        )
        print("Run without --resume to start a new session.", file=sys.stderr)
        sys.exit(1)

    # Prepare filename for team file
    team_filename = f"{sanitize_filename(programmer_name)}.md"

    # Main agent loop
    iteration = 0
    try:
        while iteration < max_iterations:
            iteration += 1

            # Check for shutdown request between iterations
            if shutdown_state["shutdown_requested"]:
                printer.console.print("[yellow]Shutting down gracefully...[/yellow]")
                break

            # Determine current state
            state = get_programmer_state(planning_dir, programmer_name)
            assigned_task = get_assigned_task(planning_dir, programmer_name)

            # Select appropriate prompt based on state
            if state == ProgrammerState.NOT_REGISTERED:
                prompt = PROMPTS["initial"].format(
                    planning_dir=planning_dir,
                    filename=team_filename,
                )
                printer.console.print(
                    "[cyan]State: Registering as available...[/cyan]"
                )
            elif state == ProgrammerState.AVAILABLE:
                prompt = PROMPTS["waiting"].format(
                    planning_dir=planning_dir,
                    filename=team_filename,
                    project_dir=project_directory,
                )
                printer.console.print(
                    "[cyan]State: Checking for task assignment...[/cyan]"
                )
            elif state in (ProgrammerState.ASSIGNED, ProgrammerState.WORKING):
                if not assigned_task:
                    # State says assigned but no task - check again
                    prompt = PROMPTS["waiting"].format(
                        planning_dir=planning_dir,
                        filename=team_filename,
                        project_dir=project_directory,
                    )
                else:
                    prompt = PROMPTS["working"].format(
                        planning_dir=planning_dir,
                        task_filename=assigned_task,
                        project_dir=project_directory,
                    )
                    printer.console.print(
                        f"[cyan]State: Working on {assigned_task}...[/cyan]"
                    )
            else:
                # Unknown state - try to register
                prompt = PROMPTS["initial"].format(
                    planning_dir=planning_dir,
                    filename=team_filename,
                )

            # Build options
            options = ClaudeAgentOptions(
                allowed_tools=["Read", "Edit", "Glob", "Write", "Bash"],
                permission_mode="acceptEdits",
                resume=current_session_id,
                system_prompt=SYSTEM_PROMPT.format(name=programmer_name),
            )

            # Run the query
            shutdown_state["query_running"] = True
            try:
                async for message in query(prompt=prompt, options=options):
                    printer.print_message(message)

                    if isinstance(message, ResultMessage):
                        current_session_id = message.session_id
                        session_info = session_manager.create_session_info(
                            session_id=message.session_id,
                            agent_name=programmer_name,
                            agent_type=AGENT_TYPE,
                            num_turns=message.num_turns,
                            total_cost_usd=message.total_cost_usd,
                        )
                        session_manager.save_session(session_info)
            finally:
                shutdown_state["query_running"] = False

            # Check for shutdown after query completes
            if shutdown_state["shutdown_requested"]:
                printer.console.print("[yellow]Shutting down gracefully...[/yellow]")
                break

            # Determine next action based on new state
            new_state = get_programmer_state(planning_dir, programmer_name)

            if new_state == ProgrammerState.AVAILABLE:
                # Waiting for assignment - sleep before next check
                printer.console.print(
                    f"[dim]Waiting {POLL_INTERVAL}s for task assignment...[/dim]"
                )
                try:
                    await asyncio.sleep(POLL_INTERVAL)
                except asyncio.CancelledError:
                    break
            # For ASSIGNED/WORKING states, continue immediately

    except GracefulExit:
        printer.console.print("[yellow]Exiting...[/yellow]")
    except ImmediateExit:
        printer.console.print("[red]Immediate exit.[/red]")
        sys.exit(1)

    if iteration >= max_iterations:
        printer.console.print(
            f"[yellow]Reached maximum iterations ({max_iterations}). Exiting.[/yellow]"
        )


def main() -> None:
    """Entry point for the programmer agent CLI."""
    parser = argparse.ArgumentParser(
        description="Run the programmer agent for task implementation."
    )
    parser.add_argument(
        "--name",
        type=str,
        default="John Doe",
        help="Name of the programmer agent (default: John Doe)",
    )
    parser.add_argument(
        "--project-dir",
        type=str,
        required=True,
        help="Path to the project directory (planning dir is {project-dir}-planning)",
    )
    parser.add_argument(
        "--verbosity",
        type=str,
        choices=["minimal", "medium", "verbose"],
        default="medium",
        help="Output verbosity level (default: medium)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"Maximum loop iterations (default: {DEFAULT_MAX_ITERATIONS})",
    )

    # Session control flags (mutually exclusive)
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "--resume",
        action="store_true",
        help="Automatically resume existing session (fails if no session exists)",
    )
    session_group.add_argument(
        "--new-session",
        action="store_true",
        help="Start a new session (deletes existing session if present)",
    )

    args = parser.parse_args()

    asyncio.run(
        run_programmer(
            programmer_name=args.name,
            project_directory=args.project_dir,
            verbosity=args.verbosity,
            resume=args.resume,
            new_session=args.new_session,
            max_iterations=args.max_iterations,
        )
    )


if __name__ == "__main__":
    main()
