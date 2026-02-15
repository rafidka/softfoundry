"""Manager agent that coordinates project setup and guides users to start other agents."""

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from softfoundry.utils.input import read_multiline_input
from softfoundry.utils.llm import needs_user_input
from softfoundry.utils.output import create_printer
from softfoundry.utils.sessions import SessionManager, format_session_info
from softfoundry.utils.status import get_status_path, read_status, update_status

AGENT_TYPE = "manager"
POLL_INTERVAL = 60  # seconds between monitoring cycles
DEFAULT_MAX_ITERATIONS = 100


def get_system_prompt(
    project: str,
    github_repo: str,
    clone_path: str,
    status_path: Path,
    num_programmers: int,
) -> str:
    """Generate the system prompt for the manager agent."""
    # Generate programmer names
    programmer_names = [
        ("Alice Chen", "alice-chen"),
        ("Bob Smith", "bob-smith"),
        ("Carol Davis", "carol-davis"),
        ("David Lee", "david-lee"),
        ("Eve Wilson", "eve-wilson"),
    ][:num_programmers]

    programmer_commands = "\n\n".join(
        f"""**Programmer {i + 1} ({name}):**
```bash
uv run python -m softfoundry.agents.programmer \\
    --name "{name}" \\
    --github-repo {github_repo} \\
    --clone-path {clone_path} \\
    --project {project}
```"""
        for i, (name, _) in enumerate(programmer_names)
    )

    return f"""You are the Manager agent for the {project} project.

GitHub repo: {github_repo}
Local clone: {clone_path}
Status file: {status_path}
Number of programmers: {num_programmers}

Your responsibilities:
1. Project setup and task planning
2. Guiding the user to start programmer/reviewer agents
3. Monitoring project progress
4. Determining when the project is complete

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

```bash
cat > {status_path} << 'EOF'
{{
  "agent_type": "manager",
  "project": "{project}",
  "status": "working",
  "details": "Description of what you're doing",
  "last_update": "$(date -Iseconds)",
  "pid": {os.getpid()}
}}
EOF
```

## Phase 1: Setup

1. Clone the repository if not already cloned:
   ```bash
   git clone https://github.com/{github_repo} {clone_path}
   ```

2. Check for PROJECT.md:
   - If missing, collaborate with the user to create it
   - Ask questions about the project scope, tech stack, features
   - Write PROJECT.md to the repo root

3. Create GitHub labels:
   ```bash
   gh label create "status:pending" --color "fbca04" --repo {github_repo} --force
   gh label create "status:in-progress" --color "0e8a16" --repo {github_repo} --force
   gh label create "status:in-review" --color "6f42c1" --repo {github_repo} --force
   gh label create "priority:high" --color "d73a4a" --repo {github_repo} --force
   gh label create "priority:medium" --color "fbca04" --repo {github_repo} --force
   gh label create "priority:low" --color "0e8a16" --repo {github_repo} --force
   ```

4. Create issues for each task based on PROJECT.md:
   ```bash
   gh issue create --repo {github_repo} --title "Task title" --body "Description" --label "status:pending,priority:medium"
   ```

5. Create assignee labels for each programmer:
   ```bash
{chr(10).join(f'   gh label create "assignee:{slug}" --color "0366d6" --repo {github_repo} --force' for _, slug in programmer_names)}
   ```

6. Assign initial tasks to programmers by adding assignee labels to issues

## Phase 2: Instruct User to Start Agents

After setup is complete, tell the user to run these commands in separate terminal tabs:

{programmer_commands}

**Reviewer:**
```bash
uv run python -m softfoundry.agents.reviewer \\
    --github-repo {github_repo} \\
    --clone-path {clone_path} \\
    --project {project}
```

Then ask the user to type "ready" when they have started all the agents.

## Phase 3: Monitor

Once agents are running:

1. Check agent status files:
   ```bash
   cat ~/.softfoundry/agents/{project}/*.status 2>/dev/null || echo "No status files yet"
   ```

2. Check GitHub for progress:
   ```bash
   gh issue list --repo {github_repo} --state open --json number,title,labels
   gh pr list --repo {github_repo} --state open --json number,title,state
   ```

3. Report progress to the user

4. If all issues are closed and all PRs are merged, the project is complete!
   - Update your status to "exited:success"
   - Congratulate the user

## Communication

When you need user input (e.g., creating PROJECT.md), ask clear questions.
The user will respond, and you can continue from there.

Remember: Let Claude handle Git and GitHub operations directly using `gh` and `git` CLI.
"""


class GracefulExit(Exception):
    """Raised when user requests graceful shutdown."""

    pass


class ImmediateExit(Exception):
    """Raised when user requests immediate shutdown."""

    pass


def setup_signal_handlers() -> dict[str, bool]:
    """Set up signal handlers for graceful shutdown."""
    state = {"shutdown_requested": False, "query_running": False}

    def handler(signum: int, frame: object) -> None:
        if state["shutdown_requested"]:
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


def extract_assistant_text(message: AssistantMessage) -> str:
    """Extract text content from an AssistantMessage."""
    texts = []
    for block in message.content:
        if isinstance(block, TextBlock):
            texts.append(block.text)
    return "\n".join(texts)


async def run_manager(
    github_repo: str | None,
    clone_path: str | None,
    num_programmers: int | None,
    verbosity: str = "medium",
    resume: bool = False,
    new_session: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> None:
    """Run the manager agent.

    Args:
        github_repo: GitHub repository in OWNER/REPO format (prompted if None).
        clone_path: Local path to clone the repo (defaults to castings/{project}).
        num_programmers: Number of programmer agents (prompted if None).
        verbosity: Output verbosity level (minimal, medium, verbose).
        resume: If True, automatically resume existing session.
        new_session: If True, always start a new session.
        max_iterations: Maximum loop iterations (safety limit).
    """
    printer = create_printer(verbosity)
    shutdown_state = setup_signal_handlers()

    # Prompt for required values if not provided
    if not github_repo:
        github_repo = input("GitHub repository (OWNER/REPO): ").strip()
        if not github_repo:
            print("Error: GitHub repository is required.", file=sys.stderr)
            sys.exit(1)

    if num_programmers is None:
        num_str = input("Number of programmers [2]: ").strip()
        num_programmers = int(num_str) if num_str else 2

    # Derive project name from repo
    project = github_repo.split("/")[-1]

    # Default clone path
    if not clone_path:
        clone_path = f"castings/{project}"

    # Initialize status file
    status_path = get_status_path(project, "manager")
    update_status(
        status_path,
        status="starting",
        details="Initializing manager agent",
        agent_type="manager",
        project=project,
    )

    # Session management
    session_manager = SessionManager(project)
    existing_session = session_manager.get_session(AGENT_TYPE, "manager")
    current_session_id: str | None = None

    if existing_session:
        if new_session:
            session_manager.delete_session(AGENT_TYPE, "manager")
            printer.console.print("Deleted existing session.")
        elif resume:
            current_session_id = existing_session.session_id
            printer.console.print("Resuming previous session...")
        else:
            printer.console.print("Found previous session:")
            print(format_session_info(existing_session))
            response = input("Continue previous session? [y/N]: ").strip().lower()
            if response == "y":
                current_session_id = existing_session.session_id
                printer.console.print("Resuming session...")
            else:
                session_manager.delete_session(AGENT_TYPE, "manager")
                printer.console.print("Starting new session...")
    elif resume:
        print("Error: No existing session found.", file=sys.stderr)
        print("Run without --resume to start a new session.", file=sys.stderr)
        sys.exit(1)

    # Check for self-awareness (crash recovery)
    existing_status = read_status(status_path)
    resume_context = ""
    if existing_status and existing_status.get("status", "").startswith("exited:"):
        # Agent had exited, starting fresh
        pass
    elif existing_status and existing_status.get("details"):
        resume_context = f"""
IMPORTANT: You previously crashed or were interrupted.
Your last status was: {existing_status.get("status")}
You were doing: {existing_status.get("details")}
Check the current state and continue from where you left off.
"""

    # Build system prompt
    system_prompt = get_system_prompt(
        project=project,
        github_repo=github_repo,
        clone_path=clone_path,
        status_path=status_path,
        num_programmers=num_programmers,
    )

    # Initial prompt
    initial_prompt = f"""Start managing the {project} project.

GitHub repo: {github_repo}
Clone path: {clone_path}
Number of programmers: {num_programmers}

{resume_context}

Begin with Phase 1: Setup. Check if the repo is cloned, verify PROJECT.md exists,
create issues for tasks, then move to Phase 2 to instruct the user to start agents.
"""

    # Build options
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Glob", "Write", "Bash", "Grep"],
        permission_mode="acceptEdits",
        resume=current_session_id,
        system_prompt=system_prompt,
        cwd=str(Path(clone_path).resolve()) if Path(clone_path).exists() else None,
    )

    # Main loop using ClaudeSDKClient
    iteration = 0
    try:
        async with ClaudeSDKClient(options=options) as client:
            # Send initial prompt
            await client.query(initial_prompt)

            while iteration < max_iterations:
                iteration += 1

                if shutdown_state["shutdown_requested"]:
                    printer.console.print(
                        "[yellow]Shutting down gracefully...[/yellow]"
                    )
                    update_status(
                        status_path,
                        status="exited:terminated",
                        details="User requested shutdown",
                    )
                    break

                # Collect response
                last_assistant_text = ""
                shutdown_state["query_running"] = True
                try:
                    async for message in client.receive_response():
                        printer.print_message(message)

                        if isinstance(message, AssistantMessage):
                            last_assistant_text = extract_assistant_text(message)

                        if isinstance(message, ResultMessage):
                            # Save session for crash recovery
                            session_info = session_manager.create_session_info(
                                session_id=message.session_id,
                                agent_name="manager",
                                agent_type=AGENT_TYPE,
                                num_turns=message.num_turns,
                                total_cost_usd=message.total_cost_usd,
                            )
                            session_manager.save_session(session_info)

                            # Check if project is complete
                            if (
                                message.result
                                and "project complete" in message.result.lower()
                            ):
                                printer.console.print(
                                    "[bold green]Project completed successfully![/bold green]"
                                )
                                update_status(
                                    status_path,
                                    status="exited:success",
                                    details="Project completed",
                                )
                                return
                finally:
                    shutdown_state["query_running"] = False

                if shutdown_state["shutdown_requested"]:
                    printer.console.print(
                        "[yellow]Shutting down gracefully...[/yellow]"
                    )
                    update_status(
                        status_path,
                        status="exited:terminated",
                        details="User requested shutdown",
                    )
                    break

                # Determine next action: user input or continue monitoring
                if needs_user_input(last_assistant_text):
                    # Interactive: wait for user input
                    printer.console.print("[cyan]Waiting for your input...[/cyan]")
                    user_input = read_multiline_input().strip()
                    if user_input:
                        await client.query(user_input)
                    else:
                        # Empty input, ask to continue
                        await client.query("Please continue.")
                else:
                    # Non-interactive: wait and continue monitoring
                    printer.console.print(
                        f"[dim]Next check in {POLL_INTERVAL}s...[/dim]"
                    )
                    try:
                        await asyncio.sleep(POLL_INTERVAL)
                    except asyncio.CancelledError:
                        break
                    await client.query(
                        "Continue monitoring. Check agent status files and GitHub state. Report progress."
                    )

    except GracefulExit:
        printer.console.print("[yellow]Exiting...[/yellow]")
        update_status(
            status_path,
            status="exited:terminated",
            details="User requested graceful exit",
        )
    except ImmediateExit:
        printer.console.print("[red]Immediate exit.[/red]")
        update_status(
            status_path,
            status="exited:terminated",
            details="User requested immediate exit",
        )
        sys.exit(1)
    except Exception as e:
        update_status(
            status_path,
            status="exited:error",
            details=f"Error: {e}",
        )
        raise

    if iteration >= max_iterations:
        printer.console.print(
            f"[yellow]Reached maximum iterations ({max_iterations}). Exiting.[/yellow]"
        )
        update_status(
            status_path,
            status="exited:terminated",
            details=f"Reached max iterations: {max_iterations}",
        )


def main() -> None:
    """Entry point for the manager agent CLI."""
    parser = argparse.ArgumentParser(
        description="Run the manager agent for project coordination."
    )
    parser.add_argument(
        "--github-repo",
        type=str,
        help="GitHub repository in OWNER/REPO format (prompted if not provided)",
    )
    parser.add_argument(
        "--clone-path",
        type=str,
        help="Local path to clone the repo (default: castings/{project})",
    )
    parser.add_argument(
        "--num-programmers",
        type=int,
        help="Number of programmer agents (prompted if not provided)",
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
            github_repo=args.github_repo,
            clone_path=args.clone_path,
            num_programmers=args.num_programmers,
            verbosity=args.verbosity,
            resume=args.resume,
            new_session=args.new_session,
            max_iterations=args.max_iterations,
        )
    )


if __name__ == "__main__":
    main()
