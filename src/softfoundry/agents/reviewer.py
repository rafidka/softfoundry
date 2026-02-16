"""Reviewer agent that reviews PRs and merges approved code."""

import argparse
import asyncio
import os
from pathlib import Path

from claude_agent_sdk import ResultMessage

from softfoundry.utils.loop import Agent, AgentConfig

AGENT_TYPE = "reviewer"
POLL_INTERVAL = 30  # seconds to wait when no PRs to review
DEFAULT_MAX_ITERATIONS = 100


class ReviewerAgent(Agent):
    """Reviewer agent that reviews PRs and merges approved code.

    This agent:
    1. Finds open PRs to review
    2. Reviews code for correctness, quality, and style
    3. Approves and merges good code
    4. Requests changes when needed
    5. Exits when all work is complete
    """

    def __init__(
        self,
        github_repo: str,
        clone_path: str,
        project: str,
        resume: bool = False,
        new_session: bool = False,
        verbosity: str = "medium",
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ):
        """Initialize the reviewer agent.

        Args:
            github_repo: GitHub repository in OWNER/REPO format.
            clone_path: Path to the main git clone.
            project: Project name.
            resume: If True, automatically resume existing session.
            new_session: If True, force a new session.
            verbosity: Output verbosity level.
            max_iterations: Maximum loop iterations.
        """
        # Store agent-specific state
        self.github_repo = github_repo
        self.clone_path = clone_path

        # Determine working directory
        cwd = str(Path(clone_path).resolve()) if Path(clone_path).exists() else None

        # Build config and delegate to parent
        # Reviewer doesn't need Edit/Write since it only reviews
        config = AgentConfig(
            project=project,
            agent_type=AGENT_TYPE,
            agent_name="reviewer",
            allowed_tools=["Read", "Glob", "Bash", "Grep"],
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
        """Generate the system prompt for the reviewer agent."""
        return f"""You are the code reviewer for the {self.config.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}
Status file: {self._status_path}

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

```bash
cat > {self._status_path} << 'EOF'
{{
  "agent_type": "reviewer",
  "project": "{self.config.project}",
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
gh pr list --repo {self.github_repo} --state open --json number,title,author,headRefName
```

### 2. If No Open PRs

Check if project is complete:
```bash
gh issue list --repo {self.github_repo} --state open --json number
```

If no open issues and no open PRs, project is complete - exit with "exited:success".
Otherwise, wait {POLL_INTERVAL} seconds and check again.

### 3. Review Each PR

a. Get PR details:
```bash
gh pr view N --repo {self.github_repo} --json number,title,body,additions,deletions,changedFiles,headRefName
```

b. Get the diff:
```bash
gh pr diff N --repo {self.github_repo}
```

c. Check the linked issue for context (look for "Closes #X" in the PR body)

d. Fetch and checkout the branch to review locally:
```bash
cd {self.clone_path}
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
gh pr review N --repo {self.github_repo} --approve --body "LGTM! Good implementation."
gh pr merge N --repo {self.github_repo} --squash --delete-branch
```

The merge will automatically close the linked issue if the PR body contains "Closes #X".

**If changes are needed - Request changes:**
```bash
gh pr review N --repo {self.github_repo} --request-changes --body "Please address the following:

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

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()
        return f"""Start reviewing PRs for the {self.config.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}

{resume_context}

Find open PRs and start reviewing them.
"""

    def _get_resume_context(self) -> str:
        """Check status file for crash recovery context."""
        existing_status = self.read_status()
        if not existing_status:
            return ""

        status = existing_status.get("status", "")
        if status.startswith("exited:"):
            return ""  # Clean exit, no recovery needed

        pr_num = existing_status.get("current_pr")
        if pr_num:
            return f"""IMPORTANT: You previously crashed or were interrupted.
Your last status was: {status}
You were reviewing PR #{pr_num}.
Details: {existing_status.get("details", "N/A")}

Check the current state of PR #{pr_num} and continue from where you left off."""

        return ""

    def is_complete(self, result: ResultMessage) -> bool:
        """Check if all reviews are done."""
        return result.result is not None and "exited:success" in result.result.lower()

    def get_continuation_prompt(self) -> str:
        """Return the prompt to keep the agent reviewing."""
        return "Continue reviewing. Check for new PRs, review pending ones, or determine if project is complete."

    # ─────────────────────────────────────────────────────────────────────────
    # OPTIONAL OVERRIDES
    # ─────────────────────────────────────────────────────────────────────────

    def get_idle_interval(self) -> int | None:
        """Wait 30 seconds if idle (no PRs to review)."""
        current_status = self.read_status()
        if current_status:
            status = current_status.get("status", "")
            if status == "idle":
                return POLL_INTERVAL
        return None

    def on_complete(self) -> None:
        """Handle completion with custom message."""
        super().on_complete()
        self.printer.console.print(
            "[bold green]All PRs reviewed and merged![/bold green]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


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
    agent = ReviewerAgent(
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
