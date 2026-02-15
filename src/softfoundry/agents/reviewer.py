"""Reviewer agent that reviews PRs and merges approved code."""

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

AGENT_TYPE = "reviewer"
POLL_INTERVAL = 30  # seconds to wait when no PRs to review
DEFAULT_MAX_ITERATIONS = 100


def get_system_prompt(
    project: str,
    github_repo: str,
    clone_path: str,
    status_path: Path,
) -> str:
    """Generate the system prompt for the reviewer agent."""
    return f"""You are the code reviewer for the {project} project.

GitHub repo: {github_repo}
Clone path: {clone_path}
Status file: {status_path}

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

```bash
cat > {status_path} << 'EOF'
{{
  "agent_type": "reviewer",
  "project": "{project}",
  "status": "working",
  "details": "Description of what you're doing",
  "current_pr": 5,
  "last_update": "$(date -Iseconds)",
  "pid": {os.getpid()}
}}
EOF
```

Status values: starting, idle, working, exited:success, exited:error

## Workflow

### 1. Find PRs to Review

```bash
gh pr list --repo {github_repo} --state open --json number,title,author,headRefName
```

### 2. If No Open PRs

Check if project is complete:
```bash
gh issue list --repo {github_repo} --state open --json number
```

If no open issues and no open PRs, project is complete - exit with "exited:success".
Otherwise, wait {POLL_INTERVAL} seconds and check again.

### 3. Review Each PR

a. Get PR details:
```bash
gh pr view N --repo {github_repo} --json number,title,body,additions,deletions,changedFiles,headRefName
```

b. Get the diff:
```bash
gh pr diff N --repo {github_repo}
```

c. Check the linked issue for context (look for "Closes #X" in the PR body)

d. Fetch and checkout the branch to review locally:
```bash
cd {clone_path}
git fetch origin
git checkout origin/BRANCH_NAME
```

e. Review the code by reading files and understanding the changes

### 4. Review Criteria

- **Correctness**: Does the code do what the issue asks?
- **Bugs**: Are there any logic errors or edge cases?
- **Code quality**: Is the code clean and readable?
- **Style**: Does it follow the project's conventions?
- **Tests**: Are there tests if applicable?
- **Documentation**: Are changes documented if needed?

### 5. Make a Decision

**If code is good - Approve and merge:**
```bash
gh pr review N --repo {github_repo} --approve --body "LGTM! Good implementation."
gh pr merge N --repo {github_repo} --squash --delete-branch
```

The merge will automatically close the linked issue if the PR body contains "Closes #X".

**If changes are needed - Request changes:**
```bash
gh pr review N --repo {github_repo} --request-changes --body "Please address the following:

1. Issue description
2. Another issue

Let me know when these are fixed."
```

Be specific about what needs to change. The programmer will address feedback and push updates.

### 6. After Review

Update your status file and continue to the next PR, or wait if there are no more.

### 7. When All Work is Done

If:
- No open PRs
- No open issues (all closed)

Then the project is complete. Update status to "exited:success" and exit.

## Important Notes

- Be thorough but efficient
- Give constructive feedback
- Approve good code promptly to keep the project moving
- The programmers are AI agents too - be specific in feedback
- Keep your status file updated so the manager knows you're alive
- If you're unsure about something, ask the user for guidance
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


async def run_reviewer(
    github_repo: str,
    clone_path: str,
    project: str,
    verbosity: str = "medium",
    resume: bool = False,
    new_session: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> None:
    """Run the reviewer agent.

    Args:
        github_repo: GitHub repository in OWNER/REPO format.
        clone_path: Path to the main git clone.
        project: Project name.
        verbosity: Output verbosity level.
        resume: If True, automatically resume existing session.
        new_session: If True, always start a new session.
        max_iterations: Maximum loop iterations (safety limit).
    """
    printer = create_printer(verbosity)
    shutdown_state = setup_signal_handlers()

    # Initialize status file
    status_path = get_status_path(project, "reviewer")
    update_status(
        status_path,
        status="starting",
        details="Initializing reviewer agent",
        agent_type="reviewer",
        project=project,
    )

    # Session management
    session_manager = SessionManager(project)
    existing_session = session_manager.get_session(AGENT_TYPE, "reviewer")
    current_session_id: str | None = None

    if existing_session:
        if new_session:
            session_manager.delete_session(AGENT_TYPE, "reviewer")
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
                session_manager.delete_session(AGENT_TYPE, "reviewer")
                printer.console.print("Starting new session...")
    elif resume:
        print("Error: No existing session found.", file=sys.stderr)
        print("Run without --resume to start a new session.", file=sys.stderr)
        sys.exit(1)

    # Check for self-awareness (crash recovery)
    existing_status = read_status(status_path)
    resume_context = ""
    if existing_status:
        status = existing_status.get("status", "")
        if status.startswith("exited:"):
            # Agent had exited, starting fresh
            pass
        elif existing_status.get("current_pr"):
            pr_num = existing_status.get("current_pr")
            resume_context = f"""
IMPORTANT: You previously crashed or were interrupted.
Your last status was: {status}
You were reviewing PR #{pr_num}.
Details: {existing_status.get("details", "N/A")}

Check the current state of PR #{pr_num} and continue from where you left off.
"""

    # Build system prompt
    system_prompt = get_system_prompt(
        project=project,
        github_repo=github_repo,
        clone_path=clone_path,
        status_path=status_path,
    )

    # Initial prompt
    initial_prompt = f"""Start reviewing PRs for the {project} project.

GitHub repo: {github_repo}
Clone path: {clone_path}

{resume_context}

Find open PRs and start reviewing them.
"""

    # Build options
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Glob", "Bash", "Grep"],
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
                                agent_name="reviewer",
                                agent_type=AGENT_TYPE,
                                num_turns=message.num_turns,
                                total_cost_usd=message.total_cost_usd,
                            )
                            session_manager.save_session(session_info)

                            # Check if project is complete
                            if (
                                message.result
                                and "exited:success" in message.result.lower()
                            ):
                                printer.console.print(
                                    "[bold green]All PRs reviewed and merged![/bold green]"
                                )
                                update_status(
                                    status_path,
                                    status="exited:success",
                                    details="All PRs reviewed and merged",
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

                # Determine next action: user input or continue reviewing
                if needs_user_input(last_assistant_text):
                    # Interactive: wait for user input
                    printer.console.print("[cyan]Waiting for your input...[/cyan]")
                    user_input = read_multiline_input().strip()
                    if user_input:
                        await client.query(user_input)
                    else:
                        await client.query("Please continue.")
                else:
                    # Check current status to determine wait time
                    current_status = read_status(status_path)
                    if current_status:
                        status = current_status.get("status", "")
                        if status == "idle":
                            printer.console.print(
                                f"[dim]No PRs to review. Checking again in {POLL_INTERVAL}s...[/dim]"
                            )
                            try:
                                await asyncio.sleep(POLL_INTERVAL)
                            except asyncio.CancelledError:
                                break

                    await client.query(
                        "Continue reviewing. Check for new PRs, review pending ones, or determine if project is complete."
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
    """Entry point for the reviewer agent CLI."""
    parser = argparse.ArgumentParser(
        description="Run the reviewer agent for PR review and merging."
    )
    parser.add_argument(
        "--github-repo",
        type=str,
        required=True,
        help="GitHub repository in OWNER/REPO format",
    )
    parser.add_argument(
        "--clone-path",
        type=str,
        required=True,
        help="Path to the main git clone",
    )
    parser.add_argument(
        "--project",
        type=str,
        required=True,
        help="Project name",
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
        run_reviewer(
            github_repo=args.github_repo,
            clone_path=args.clone_path,
            project=args.project,
            verbosity=args.verbosity,
            resume=args.resume,
            new_session=args.new_session,
            max_iterations=args.max_iterations,
        )
    )


if __name__ == "__main__":
    main()
