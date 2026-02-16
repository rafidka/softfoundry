"""Programmer agent that works on GitHub issues and creates PRs."""

import argparse
import asyncio
import os
from pathlib import Path

from claude_agent_sdk import ResultMessage

from softfoundry.utils.loop import Agent, AgentConfig
from softfoundry.utils.status import sanitize_name

AGENT_TYPE = "programmer"
DEFAULT_MAX_ITERATIONS = 100


class ProgrammerAgent(Agent):
    """Programmer agent that works on GitHub issues and creates PRs.

    This agent:
    1. Finds tasks assigned to it via GitHub labels
    2. Works on tasks in a git worktree
    3. Creates PRs for completed work
    4. Addresses review feedback
    5. Exits when all tasks are complete
    """

    def __init__(
        self,
        name: str,
        github_repo: str,
        clone_path: str,
        project: str,
        resume: bool = False,
        new_session: bool = False,
        verbosity: str = "medium",
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ):
        """Initialize the programmer agent.

        Args:
            name: Programmer name (e.g., "Alice Chen").
            github_repo: GitHub repository in OWNER/REPO format.
            clone_path: Path to the main git clone.
            project: Project name.
            resume: If True, automatically resume existing session.
            new_session: If True, force a new session.
            verbosity: Output verbosity level.
            max_iterations: Maximum loop iterations.
        """
        # Store agent-specific state
        self.name = name
        self.name_slug = sanitize_name(name)
        self.github_repo = github_repo
        self.clone_path = clone_path
        self.worktree_path = f"{clone_path}-{self.name_slug}"

        # Determine working directory
        cwd = self.worktree_path if Path(self.worktree_path).exists() else clone_path

        # Build config and delegate to parent
        config = AgentConfig(
            project=project,
            agent_type=AGENT_TYPE,
            agent_name=name,
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
        """Generate the system prompt for the programmer agent."""
        return f"""You are {self.name}, a programmer working on the {self.config.project} project.

GitHub repo: {self.github_repo}
Main clone: {self.clone_path}
Your worktree: {self.worktree_path}
Status file: {self._status_path}
Your assignee label: assignee:{self.name_slug}

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

```bash
cat > {self._status_path} << 'EOF'
{{
  "agent_type": "programmer",
  "name": "{self.name}",
  "project": "{self.config.project}",
  "status": "working",
  "details": "Description of what you're doing",
  "current_issue": 3,
  "current_pr": null,
  "last_update": "$(date -Iseconds)",
  "pid": {os.getpid()}
}}
EOF
```

Status values: starting, idle, working, waiting_review, addressing_feedback, exited:success, exited:error

## Workflow

### 1. Find Tasks Assigned to You

```bash
gh issue list --repo {self.github_repo} --label "assignee:{self.name_slug}" --label "status:pending" --json number,title,body
```

### 2. If No Assigned Tasks, Help Others

Pick any unassigned pending task:
```bash
gh issue list --repo {self.github_repo} --label "status:pending" --json number,title,body
```

Take the first one that doesn't have another programmer's assignee label.

### 3. If No Pending Tasks

Check if project is complete:
```bash
gh issue list --repo {self.github_repo} --state open --json number
```

If no open issues, exit gracefully with status "exited:success".

### 4. Start a Task

a. Create your worktree (if not exists):
```bash
cd {self.clone_path}
git fetch origin
git worktree add {self.worktree_path} -b feature/issue-N-slug origin/main
```

Or if worktree exists, just create a new branch:
```bash
cd {self.worktree_path}
git fetch origin
git checkout -b feature/issue-N-slug origin/main
```

b. Update issue labels:
```bash
gh issue edit N --repo {self.github_repo} --remove-label "status:pending" --add-label "status:in-progress"
```

c. Comment on the issue:
```bash
gh issue comment N --repo {self.github_repo} --body "Starting implementation"
```

d. Update your status file with current_issue

### 5. Implement the Task

- Work in your worktree: {self.worktree_path}
- Follow the project's coding standards
- Write tests if applicable
- Commit frequently with clear messages

Periodically update:
- Issue with progress: `gh issue comment N --body "Progress: ..."`
- Your status file

### 6. Create a PR

When implementation is complete:

```bash
cd {self.worktree_path}
git add -A
git commit -m "feat: description of changes"
git push -u origin feature/issue-N-slug
```

Create the PR:
```bash
gh pr create --repo {self.github_repo} --title "Title" --body "## Summary

Description

Closes #N"
```

Update labels:
```bash
gh issue edit N --repo {self.github_repo} --remove-label "status:in-progress" --add-label "status:in-review"
```

Update your status file with current_pr and status "waiting_review".

### 7. Wait for Review

Check PR status:
```bash
gh pr view PR_NUMBER --repo {self.github_repo} --json state,reviewDecision,reviews
```

- If `reviewDecision` is "APPROVED" and merged: clean up and find next task
- If `reviewDecision` is "CHANGES_REQUESTED": address feedback and push updates
- If still waiting: check again in 30 seconds

### 8. Handle Conflicts

If PR has conflicts:
```bash
cd {self.worktree_path}
git fetch origin
git rebase origin/main
# Resolve conflicts if any
git push --force-with-lease
```

### 9. Clean Up After Merge

```bash
cd {self.clone_path}
git worktree remove {self.worktree_path} --force
git branch -D feature/issue-N-slug
```

Or just create a new branch for the next task in your worktree.

## Important Notes

- Always work in your worktree, not the main clone
- Keep your status file updated so the manager knows you're alive
- If you crash, read your status file on restart to resume
- When all tasks are done, exit with status "exited:success"
- If you need clarification on a task, ask the user
"""

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()
        return f"""Start working as {self.name} on the {self.config.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}
Your worktree: {self.worktree_path}

{resume_context}

Find a task to work on and start implementing it.
"""

    def _get_resume_context(self) -> str:
        """Check status file for crash recovery context."""
        existing_status = self.read_status()
        if not existing_status:
            return ""

        status = existing_status.get("status", "")
        if status.startswith("exited:"):
            return ""  # Clean exit, no recovery needed

        issue_num = existing_status.get("current_issue")
        pr_num = existing_status.get("current_pr")

        if issue_num:
            pr_info = (
                f"You had created PR #{pr_num}." if pr_num else "No PR was created yet."
            )
            return f"""IMPORTANT: You previously crashed or were interrupted.
Your last status was: {status}
You were working on issue #{issue_num}.
{pr_info}
Details: {existing_status.get("details", "N/A")}

Check the current state of issue #{issue_num} and continue from where you left off."""

        return ""

    def is_complete(self, result: ResultMessage) -> bool:
        """Check if the programmer has finished all tasks."""
        return result.result is not None and "exited:success" in result.result.lower()

    def get_continuation_prompt(self) -> str:
        """Return the prompt to keep the agent working."""
        return "Continue working. Check task status, implement, or check for review feedback."

    # ─────────────────────────────────────────────────────────────────────────
    # OPTIONAL OVERRIDES
    # ─────────────────────────────────────────────────────────────────────────

    def get_idle_interval(self) -> int | None:
        """Wait 30s if idle or waiting for PR review."""
        current_status = self.read_status()
        if current_status:
            status = current_status.get("status", "")
            if status in ("idle", "waiting_review"):
                return 30
        return None

    def on_complete(self) -> None:
        """Handle completion with custom message."""
        super().on_complete()
        self.printer.console.print("[bold green]All tasks completed![/bold green]")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


async def run_programmer(
    name: str,
    github_repo: str,
    clone_path: str,
    project: str,
    verbosity: str = "medium",
    resume: bool = False,
    new_session: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> None:
    """Run the programmer agent.

    Args:
        name: Programmer name (e.g., "Alice Chen").
        github_repo: GitHub repository in OWNER/REPO format.
        clone_path: Path to the main git clone.
        project: Project name.
        verbosity: Output verbosity level.
        resume: If True, automatically resume existing session.
        new_session: If True, always start a new session.
        max_iterations: Maximum loop iterations (safety limit).
    """
    agent = ProgrammerAgent(
        name=name,
        github_repo=github_repo,
        clone_path=clone_path,
        project=project,
        resume=resume,
        new_session=new_session,
        verbosity=verbosity,
        max_iterations=max_iterations,
    )
    await agent.run()


def main() -> None:
    """Entry point for the programmer agent CLI."""
    parser = argparse.ArgumentParser(
        description="Run a programmer agent for task implementation."
    )
    parser.add_argument(
        "--name",
        type=str,
        required=True,
        help="Name of the programmer (e.g., 'Alice Chen')",
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
        run_programmer(
            name=args.name,
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
