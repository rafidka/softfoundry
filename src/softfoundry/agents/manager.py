"""Manager agent that coordinates project setup and guides users to start other agents."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import ResultMessage

from softfoundry.utils.loop import Agent, AgentConfig

AGENT_TYPE = "manager"
POLL_INTERVAL = 60  # seconds between monitoring cycles
DEFAULT_MAX_ITERATIONS = 100


class ManagerAgent(Agent):
    """Manager agent that coordinates project setup and monitors progress.

    This agent:
    1. Sets up the project (clone repo, create PROJECT.md, create issues)
    2. Guides the user to start programmer and reviewer agents
    3. Monitors project progress
    4. Determines when the project is complete
    """

    def __init__(
        self,
        github_repo: str,
        clone_path: str,
        num_programmers: int,
        project: str,
        resume: bool = False,
        new_session: bool = False,
        verbosity: str = "medium",
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ):
        """Initialize the manager agent.

        Args:
            github_repo: GitHub repository in OWNER/REPO format.
            clone_path: Local path to clone the repo.
            num_programmers: Number of programmer agents.
            project: Project name (derived from repo).
            resume: If True, automatically resume existing session.
            new_session: If True, force a new session.
            verbosity: Output verbosity level.
            max_iterations: Maximum loop iterations.
        """
        # Store agent-specific state
        self.github_repo = github_repo
        self.clone_path = clone_path
        self.num_programmers = num_programmers

        # Generate programmer names
        self.programmer_names = [
            ("Alice Chen", "alice-chen"),
            ("Bob Smith", "bob-smith"),
            ("Carol Davis", "carol-davis"),
            ("David Lee", "david-lee"),
            ("Eve Wilson", "eve-wilson"),
        ][:num_programmers]

        # Determine working directory
        cwd = str(Path(clone_path).resolve()) if Path(clone_path).exists() else None

        # Build config and delegate to parent
        config = AgentConfig(
            project=project,
            agent_type=AGENT_TYPE,
            agent_name="manager",
            allowed_tools=["Read", "Edit", "Glob", "Write", "Bash", "Grep"],
            permission_mode="acceptEdits",
            cwd=cwd,
            max_iterations=max_iterations,
            resume=resume,
            new_session=new_session,
            verbosity=verbosity,
        )
        super().__init__(config)

    # ─────────────────────────────────────────────────────────────────────────
    # ABSTRACT METHOD IMPLEMENTATIONS
    # ─────────────────────────────────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        """Generate the system prompt for the manager agent."""
        programmer_commands = "\n\n".join(
            f"""**Programmer {i + 1} ({name}):**
```bash
uv run python -m softfoundry.agents.programmer \\
    --name "{name}" \\
    --github-repo {self.github_repo} \\
    --clone-path {self.clone_path} \\
    --project {self.config.project}
```"""
            for i, (name, _) in enumerate(self.programmer_names)
        )

        assignee_labels = "\n".join(
            f'   gh label create "assignee:{slug}" --color "0366d6" --repo {self.github_repo} --force'
            for _, slug in self.programmer_names
        )

        return f"""You are the Manager agent for the {self.config.project} project.

GitHub repo: {self.github_repo}
Local clone: {self.clone_path}
Status file: {self._status_path}
Number of programmers: {self.num_programmers}

Your responsibilities:
1. Project setup and task planning
2. Guiding the user to start programmer/reviewer agents
3. Monitoring project progress
4. Determining when the project is complete

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

```bash
cat > {self._status_path} << 'EOF'
{{
  "agent_type": "manager",
  "project": "{self.config.project}",
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
   git clone https://github.com/{self.github_repo} {self.clone_path}
   ```

2. Check for PROJECT.md:
   - If missing, collaborate with the user to create it
   - Ask questions about the project scope, tech stack, features
   - Write PROJECT.md to the repo root

3. Create GitHub labels:
   ```bash
   gh label create "status:pending" --color "fbca04" --repo {self.github_repo} --force
   gh label create "status:in-progress" --color "0e8a16" --repo {self.github_repo} --force
   gh label create "status:in-review" --color "6f42c1" --repo {self.github_repo} --force
   gh label create "priority:high" --color "d73a4a" --repo {self.github_repo} --force
   gh label create "priority:medium" --color "fbca04" --repo {self.github_repo} --force
   gh label create "priority:low" --color "0e8a16" --repo {self.github_repo} --force
   ```

4. Create issues for each task based on PROJECT.md:
   ```bash
   gh issue create --repo {self.github_repo} --title "Task title" --body "Description" --label "status:pending,priority:medium"
   ```

5. Create assignee labels for each programmer:
   ```bash
{assignee_labels}
   ```

6. Assign initial tasks to programmers by adding assignee labels to issues

## Phase 2: Instruct User to Start Agents

After setup is complete, tell the user to run these commands in separate terminal tabs:

{programmer_commands}

**Reviewer:**
```bash
uv run python -m softfoundry.agents.reviewer \\
    --github-repo {self.github_repo} \\
    --clone-path {self.clone_path} \\
    --project {self.config.project}
```

Then ask the user to type "ready" when they have started all the agents.

## Phase 3: Monitor

Once agents are running:

1. Check agent status files:
   ```bash
   cat ~/.softfoundry/agents/{self.config.project}/*.status 2>/dev/null || echo "No status files yet"
   ```

2. Check GitHub for progress:
   ```bash
   gh issue list --repo {self.github_repo} --state open --json number,title,labels
   gh pr list --repo {self.github_repo} --state open --json number,title,state
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

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()
        return f"""Start managing the {self.config.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}
Number of programmers: {self.num_programmers}

{resume_context}

Begin with Phase 1: Setup. Check if the repo is cloned, verify PROJECT.md exists,
create issues for tasks, then move to Phase 2 to instruct the user to start agents.
"""

    def _get_resume_context(self) -> str:
        """Check status file for crash recovery context."""
        existing_status = self.read_status()
        if not existing_status:
            return ""

        status = existing_status.get("status", "")
        if status.startswith("exited:"):
            return ""  # Clean exit, no recovery needed

        if existing_status.get("details"):
            return f"""IMPORTANT: You previously crashed or were interrupted.
Your last status was: {status}
You were doing: {existing_status.get("details")}
Check the current state and continue from where you left off."""

        return ""

    def is_complete(self, result: ResultMessage) -> bool:
        """Check if the project is complete."""
        return result.result is not None and "project complete" in result.result.lower()

    def get_continuation_prompt(self) -> str:
        """Return the prompt to keep the agent monitoring."""
        return "Continue monitoring. Check agent status files and GitHub state. Report progress."

    # ─────────────────────────────────────────────────────────────────────────
    # OPTIONAL OVERRIDES
    # ─────────────────────────────────────────────────────────────────────────

    def get_idle_interval(self) -> int | None:
        """Wait 60 seconds between monitoring cycles."""
        return POLL_INTERVAL

    def on_complete(self) -> None:
        """Handle completion with custom message."""
        super().on_complete()
        self.printer.console.print(
            "[bold green]Project completed successfully![/bold green]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


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

    agent = ManagerAgent(
        github_repo=github_repo,
        clone_path=clone_path,
        num_programmers=num_programmers,
        project=project,
        resume=resume,
        new_session=new_session,
        verbosity=verbosity,
        max_iterations=max_iterations,
    )
    await agent.run()


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
