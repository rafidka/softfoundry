"""Manager agent that coordinates project planning and task assignment."""

import argparse
import asyncio
import signal
import sys

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from softfoundry.utils.output import create_printer
from softfoundry.utils.sessions import SessionManager, format_session_info
from softfoundry.utils.state import ManagerState, get_manager_state

AGENT_TYPE = "manager"
POLL_INTERVAL = 30  # seconds to wait when monitoring progress
DEFAULT_MAX_ITERATIONS = 100  # prevent infinite loops during testing

# System prompt for the manager agent
SYSTEM_PROMPT = """
You are {name}, a manager of a team of AI agents. Your responsibility is to
manage the project planning and execution.

Your workflow:
1. Read the project description and create tasks
2. Assign tasks to available programmers
3. Monitor progress until all tasks are completed
4. Generate a final project summary

You are NOT responsible for creating programmer agents. Programmers will register
themselves in the team directory when they become available.

Always update task files and team files as you assign work and track progress.
"""

# State-specific prompts
PROMPTS = {
    "initial": """
Read the project description at `{planning_dir}/PROJECT.md`.

Create task files in `{planning_dir}/tasks/` based on the project requirements.
Use the template at `{planning_dir}/tasks/TEMPLATE.md` as a guide.

Each task should be a separate markdown file with:
- A clear description of what needs to be done
- Status set to PENDING
- Assigned To set to Unassigned
- Appropriate priority (HIGH, MEDIUM, LOW)

After creating the tasks, report what tasks you've created.
""",
    "assigning": """
Check for available programmers in `{planning_dir}/team/`.
Look for team member files where Status is AVAILABLE.

For each available programmer, assign a pending task by:
1. Updating the task file: set "Assigned To" to the programmer's name, set Status to IN_PROGRESS
2. Updating the programmer's team file: set Status to ASSIGNED, set "Assigned Task" to the task filename

Report which tasks you've assigned to which programmers.

If there are no available programmers, report that you're waiting for programmers.
If there are no pending tasks, report that all tasks are assigned.
""",
    "monitoring": """
Check the status of all tasks in `{planning_dir}/tasks/`.
Check the status of all team members in `{planning_dir}/team/`.

Report on overall project progress:
- How many tasks are completed vs in progress vs pending
- Which programmers are working on what

If any programmers have become available (Status: AVAILABLE) and there are 
pending tasks, assign them.
""",
    "completed": """
All tasks have been completed!

Generate a final project summary:
1. List all completed tasks
2. Summarize what was accomplished
3. Note any issues or observations from the Comments in task files

The project is now complete.
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
        A dict with state tracking keys for shutdown handling.
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


async def run_manager(
    manager_name: str,
    project_directory: str,
    verbosity: str = "medium",
    resume: bool = False,
    new_session: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> None:
    """Run the manager agent in a continuous loop.

    Args:
        manager_name: Name of the manager agent.
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
    existing_session = session_manager.get_session(AGENT_TYPE, manager_name)
    current_session_id: str | None = None

    if existing_session:
        if new_session:
            session_manager.delete_session(AGENT_TYPE, manager_name)
            printer.console.print(f"Deleted existing session for {manager_name}.")
        elif resume:
            current_session_id = existing_session.session_id
            printer.console.print(f"Resuming session for {manager_name}...")
        else:
            printer.console.print(f"Found previous session for {manager_name}:")
            print(format_session_info(existing_session))
            response = input("Continue previous session? [y/N]: ").strip().lower()
            if response == "y":
                current_session_id = existing_session.session_id
                printer.console.print("Resuming session...")
            else:
                session_manager.delete_session(AGENT_TYPE, manager_name)
                printer.console.print("Starting new session...")
    elif resume:
        print(f"Error: No existing session found for {manager_name}.", file=sys.stderr)
        print("Run without --resume to start a new session.", file=sys.stderr)
        sys.exit(1)

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
            state = get_manager_state(planning_dir)

            # Check for project completion
            if state == ManagerState.COMPLETED:
                # Run one final query to generate summary
                prompt = PROMPTS["completed"].format(planning_dir=planning_dir)
                printer.console.print(
                    "[bold green]State: Project completed! Generating summary...[/bold green]"
                )

                options = ClaudeAgentOptions(
                    allowed_tools=["Read", "Glob"],
                    permission_mode="acceptEdits",
                    resume=current_session_id,
                    system_prompt=SYSTEM_PROMPT.format(name=manager_name),
                )

                shutdown_state["query_running"] = True
                try:
                    async for message in query(prompt=prompt, options=options):
                        printer.print_message(message)
                        if isinstance(message, ResultMessage):
                            current_session_id = message.session_id
                            session_info = session_manager.create_session_info(
                                session_id=message.session_id,
                                agent_name=manager_name,
                                agent_type=AGENT_TYPE,
                                num_turns=message.num_turns,
                                total_cost_usd=message.total_cost_usd,
                            )
                            session_manager.save_session(session_info)
                finally:
                    shutdown_state["query_running"] = False

                printer.console.print(
                    "[bold green]Project completed successfully![/bold green]"
                )
                break

            # Select appropriate prompt based on state
            if state == ManagerState.INITIAL:
                prompt = PROMPTS["initial"].format(planning_dir=planning_dir)
                printer.console.print(
                    "[cyan]State: Creating tasks from project...[/cyan]"
                )
            elif state == ManagerState.ASSIGNING:
                prompt = PROMPTS["assigning"].format(planning_dir=planning_dir)
                printer.console.print(
                    "[cyan]State: Assigning tasks to programmers...[/cyan]"
                )
            elif state == ManagerState.MONITORING:
                prompt = PROMPTS["monitoring"].format(planning_dir=planning_dir)
                printer.console.print("[cyan]State: Monitoring progress...[/cyan]")
            else:
                # Fallback
                prompt = PROMPTS["monitoring"].format(planning_dir=planning_dir)

            # Build options
            options = ClaudeAgentOptions(
                allowed_tools=["Read", "Edit", "Glob", "Write"],
                permission_mode="acceptEdits",
                resume=current_session_id,
                system_prompt=SYSTEM_PROMPT.format(name=manager_name),
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
                            agent_name=manager_name,
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
            new_state = get_manager_state(planning_dir)

            if new_state == ManagerState.MONITORING:
                # All tasks assigned, waiting for completion - sleep before next check
                printer.console.print(
                    f"[dim]Monitoring progress. Checking again in {POLL_INTERVAL}s...[/dim]"
                )
                try:
                    await asyncio.sleep(POLL_INTERVAL)
                except asyncio.CancelledError:
                    break
            # For INITIAL/ASSIGNING states, continue immediately

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
    """Entry point for the manager agent CLI."""
    parser = argparse.ArgumentParser(
        description="Run the manager agent for project coordination."
    )
    parser.add_argument(
        "--name",
        type=str,
        default="Alice Chen",
        help="Name of the manager agent (default: Alice Chen)",
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
        run_manager(
            manager_name=args.name,
            project_directory=args.project_dir,
            verbosity=args.verbosity,
            resume=args.resume,
            new_session=args.new_session,
            max_iterations=args.max_iterations,
        )
    )


if __name__ == "__main__":
    main()
