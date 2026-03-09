"""Programmer agent that works on GitHub issues and creates PRs.

Each agent run drives a single issue to completion (claim -> implement ->
PR -> review -> merge), then exits. A Python outer loop in run_programmer()
handles re-launching the agent for subsequent tasks with fresh sessions.
"""

import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import ResultMessage
from rich.console import Console

from softfoundry.agents.base import Agent, AgentConfig
from softfoundry.mcp import create_orchestrator_server
from softfoundry.utils.github import LABEL_COLORS, format_signature
from softfoundry.utils.status import sanitize_name

AGENT_TYPE = "programmer"
DEFAULT_MAX_ITERATIONS = 100
DEFAULT_TASK_DELAY = 60

# Exit signals the agent can output
EXIT_TASK_COMPLETE = "exit:task_complete"
EXIT_NO_TASKS = "exit:no_tasks"
EXIT_ALL_DONE = "exit:all_done"
EXIT_SIGNALS = (EXIT_TASK_COMPLETE, EXIT_NO_TASKS, EXIT_ALL_DONE)


class ProgrammerAgent(Agent):
    """Programmer agent that drives a single issue to completion.

    This agent:
    1. Claims an unassigned task from the epic
    2. Implements the task in a git worktree
    3. Creates a PR and addresses review feedback
    4. Merges the PR when approved
    5. Updates its memory file and exits

    The outer loop in run_programmer() handles re-launching for new tasks.
    """

    def __init__(
        self,
        name: str,
        github_repo: str,
        clone_path: str,
        project: str,
        epic: int,
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
            epic: GitHub issue number of the epic to work on.
            resume: If True, automatically resume existing session.
            new_session: If True, force a new session.
            verbosity: Output verbosity level.
            max_iterations: Maximum loop iterations.
        """
        # Store agent-specific state
        self.name = name
        self.name_slug = sanitize_name(name)
        self.github_repo = github_repo
        self.clone_path = str(Path(clone_path).resolve())  # Always use absolute path
        self.project = project
        self.epic = epic
        self.worktree_path = f"{self.clone_path}-{self.name_slug}"
        self.exit_reason: str | None = None

        # Determine working directory (prefer worktree if exists, then clone)
        if Path(self.worktree_path).exists():
            cwd = self.worktree_path
        elif Path(self.clone_path).exists():
            cwd = self.clone_path
        else:
            cwd = None

        # Create MCP orchestrator server
        orchestrator = create_orchestrator_server(
            name="orchestrator",
            github_repo=github_repo,
        )

        # Build config and delegate to parent
        config = AgentConfig(
            namespace=project,
            agent_type=AGENT_TYPE,
            agent_name=name,
            allowed_tools=[
                "Read",
                "Edit",
                "Glob",
                "Write",
                "Bash",
                "Grep",
                # Epic/Issue tools
                "mcp__orchestrator__get_epic_status",
                "mcp__orchestrator__get_sub_issue",
                "mcp__orchestrator__list_available_sub_issues",
                "mcp__orchestrator__list_my_sub_issues",
                "mcp__orchestrator__claim_sub_issue",
                "mcp__orchestrator__update_sub_issue_status",
                # PR tools
                "mcp__orchestrator__get_pr_status",
                "mcp__orchestrator__list_my_prs",
                "mcp__orchestrator__mark_feedback_addressed",
                "mcp__orchestrator__get_pr_feedback",
                "mcp__orchestrator__create_pr",
                "mcp__orchestrator__merge_pr",
                # Comment tools
                "mcp__orchestrator__comment_on_issue",
                "mcp__orchestrator__comment_on_pr",
                # Label tools
                "mcp__orchestrator__create_label",
                # Activity tools
                "mcp__orchestrator__log_activity",
                "mcp__orchestrator__get_activity_log",
            ],
            mcp_servers={"orchestrator": orchestrator},
            permission_mode="acceptEdits",
            cwd=cwd,
            max_iterations=max_iterations,
            memory_enabled=True,
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
        return f"""You are {self.name}, a programmer working on the {self.project} project.

GitHub repo: {self.github_repo}
Main clone: {self.clone_path}
Your worktree: {self.worktree_path}
Status file: {self._status_path}
Epic: #{self.epic}
Your assignee label: assignee:{self.name_slug}

## MCP Tools

You have MCP tools for coordinating with other agents. Use these instead of raw `gh` CLI commands:

**Epic/Issue Tools:**
- `get_epic_status(epic_number)` - Get epic with all sub-issue statuses
- `get_sub_issue(epic_number, sub_issue_number)` - Get sub-issue details
- `list_available_sub_issues(epic_number, priority)` - List unassigned sub-issues (auto-filters by dependency)
- `list_my_sub_issues(epic_number, agent_name)` - List your assigned sub-issues
- `claim_sub_issue(epic_number, sub_issue_number, agent_name)` - Claim a sub-issue
- `update_sub_issue_status(epic_number, sub_issue_number, new_status)` - Update status

**PR Tools:**
- `get_pr_status(pr_number)` - Get PR status (`has_feedback`, `is_approved`, `has_conflicts`)
- `list_my_prs(author_name)` - List your open PRs
- `mark_feedback_addressed(pr_number, agent_name, agent_type, comment)` - Mark feedback addressed
- `get_pr_feedback(pr_number)` - Get reviews and inline diff-level comments
- `create_pr(title, body, head_branch, base_branch, agent_name, agent_type, labels)` - Create a PR
- `merge_pr(pr_number, method, delete_branch)` - Merge a PR

**Other Tools:**
- `comment_on_issue(issue_number, agent_name, agent_type, comment)` - Comment on issue
- `comment_on_pr(pr_number, agent_name, agent_type, comment)` - Comment on PR
- `create_label(name, color, description)` - Create or update a label
- `log_activity(epic_number, agent_name, agent_type, event_type, message, issue_number, pr_number)` - Log activity

All MCP tools are prefixed with `mcp__orchestrator__` when calling them.

## Field Reference

**Sub-issue fields:** `state` (open/closed), `sf_status` (pending/in-progress/in-review, null when closed), `assignee` (agent slug), `reviewer` (reviewer slug), `linked_pr` (PR number).

## Status File

Update your status file frequently:
```bash
cat > {self._status_path} << 'EOF'
{{
  "agent_type": "programmer",
  "name": "{self.name}",
  "project": "{self.project}",
  "status": "working",
  "details": "Description of what you're doing",
  "current_issue": 3,
  "current_pr": null,
  "last_update": "$(date -Iseconds)",
  "pid": {os.getpid()}
}}
EOF
```

Status values: starting, idle, working, waiting_review, addressing_feedback, exited:success

## Multi-Agent Context

Multiple AI agents share the SAME GitHub account. Always identify yourself with your signature: {format_signature(self.name, "Programmer")}
Coordinate via labels (`assignee:{{slug}}`, `reviewer:{{slug}}`). Check the Author field in PRs.

## Workflow

### 1. Claim a Task

Find and claim an unassigned sub-issue:
```
list_available_sub_issues(epic_number={self.epic}, priority="")
claim_sub_issue(epic_number={self.epic}, sub_issue_number=N, agent_name="{self.name}")
```

If no tasks are available, check the epic status. If all sub-issues are closed, exit with `exit:all_done`. Otherwise exit with `exit:no_tasks`.

Log the claim:
```
log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="programmer", event_type="claimed", message="Starting work on this issue", issue_number=N, pr_number=0)
```

### 2. Set Up Worktree

Create or reset your worktree:
```bash
cd {self.clone_path}
git fetch origin
# Create worktree if needed:
git worktree add {self.worktree_path} -b feature/issue-N-slug origin/main
# Or if worktree exists, create new branch:
cd {self.worktree_path}
git fetch origin
git checkout -b feature/issue-N-slug origin/main
```

Comment on the issue and update your status file with `current_issue`.

### 3. Implement

- Work in your worktree: `{self.worktree_path}`
- Follow project coding standards, write tests if applicable
- Commit frequently with clear messages
- Update status file and log progress periodically

### 4. Create PR

```bash
cd {self.worktree_path}
git add -A && git commit -m "feat: description"
git fetch origin && git rebase origin/main
git push -u origin feature/issue-N-slug
```

Create PR with your assignee label:
```
create_pr(title="Title", body="## Summary\\n\\nDescription\\n\\nCloses #N", head_branch="feature/issue-N-slug", base_branch="main", agent_name="{self.name}", agent_type="programmer", labels="assignee:{self.name_slug}")
update_sub_issue_status(epic_number={self.epic}, sub_issue_number=N, new_status="in-review")
```

Update status to `waiting_review`.

### 5. Handle Review

Check PR status with `get_pr_status(pr_number=PR)`.

- **Merged**: Go to step 7 (clean up)
- **`has_feedback`**: Read feedback with `get_pr_feedback`, make fixes, commit, push, call `mark_feedback_addressed`
- **`is_approved`**: Merge with `merge_pr(pr_number=PR, method="squash", delete_branch="true")`. If conflicts, go to step 6.
- **`has_conflicts`**: Go to step 6
- **Not yet reviewed**: Wait and check again

### 6. Handle Conflicts

```bash
cd {self.worktree_path}
git fetch origin && git rebase origin/main
git push --force-with-lease
```

Comment on PR that conflicts are resolved.

### 7. Clean Up After Merge

```bash
cd {self.clone_path}
git worktree remove {self.worktree_path} --force
git branch -D feature/issue-N-slug
```

Log the merge activity.

### 8. Update Memory and Exit

After cleanup (or if no tasks were found):

1. Update your memory file with what you learned (codebase patterns, architecture, useful commands, gotchas). Keep it organized by topic, not chronologically.

2. Update your status file to `exited:success`.

3. Output your exit signal as the very last thing in your response:
   - `exit:task_complete` -- PR merged, task done
   - `exit:no_tasks` -- No unassigned tasks available
   - `exit:all_done` -- All epic sub-issues are closed

## Important Notes

- Always work in your worktree, not the main clone
- Keep your status file updated (the manager monitors heartbeats)
- One task per session -- implement, get reviewed, merge, then exit
- When all tasks are done, exit with `exit:all_done`
"""

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()
        has_memory = self.memory_path and self.memory_path.exists()

        parts = [
            f"Start working as {self.name} on the {self.project} project.",
            "",
            f"GitHub repo: {self.github_repo}",
            f"Clone path: {self.clone_path}",
            f"Your worktree: {self.worktree_path}",
            f"Epic: #{self.epic}",
        ]

        if resume_context:
            parts.append("")
            parts.append(resume_context)
        else:
            parts.append("")
            # First check for existing open PRs
            parts.append(
                "First, check if you have any existing open PRs "
                f'(`list_my_prs(author_name="{self.name}")`). '
                "If you have an open PR, check its status and drive it to completion "
                "(address feedback, merge when approved). "
                "Do NOT start a new task while you have an open PR."
            )
            parts.append("")
            parts.append(
                "If you have no open PRs, find and claim a sub-issue from the epic."
            )

        if not has_memory:
            parts.append("")
            parts.append(
                "This is your first session. "
                f'Create your assignee label: `create_label(name="assignee:{self.name_slug}", '
                f'color="{LABEL_COLORS["assignee"]}", description="")`'
            )
            parts.append(
                f"Then log your start: `log_activity(epic_number={self.epic}, "
                f'agent_name="{self.name}", agent_type="programmer", '
                f'event_type="started", message="Started and ready to work", '
                f"issue_number=0, pr_number=0)`"
            )

        return "\n".join(parts)

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
        """Check if the programmer has finished its task."""
        text = (result.result or "").lower()
        for signal in EXIT_SIGNALS:
            if signal in text:
                # Store the reason (e.g., "task_complete", "no_tasks", "all_done")
                self.exit_reason = signal.split(":")[1]
                return True
        # Backward compatibility
        if "exited:success" in text:
            self.exit_reason = "success"
            return True
        return False

    def get_continuation_prompt(self) -> str:
        """Return the prompt to keep the agent working."""
        return (
            "Continue working. "
            "Check task status, implement, or check for review feedback."
        )

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
        """Handle completion with exit-reason-specific messaging."""
        reason = self.exit_reason or "success"

        if reason == "task_complete":
            self.update_status("exited:success", "Task completed, PR merged")
            self.printer.console.print(
                "[bold green]Task completed! PR merged.[/bold green]"
            )
        elif reason == "no_tasks":
            self.update_status("exited:success", "No tasks available")
            self.printer.console.print("[yellow]No tasks available to claim.[/yellow]")
        elif reason == "all_done":
            self.update_status("exited:success", "All epic tasks completed")
            self.printer.console.print("[bold green]All tasks completed![/bold green]")
        else:
            super().on_complete()


# ─────────────────────────────────────────────────────────────────────────────
# OUTER LOOP
# ─────────────────────────────────────────────────────────────────────────────


async def run_programmer(
    name: str,
    github_repo: str,
    clone_path: str,
    project: str,
    epic: int,
    verbosity: str = "medium",
    resume: bool = False,
    new_session: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    task_delay: int = DEFAULT_TASK_DELAY,
) -> None:
    """Run the programmer agent in a loop, one task per session.

    Each iteration creates a fresh ProgrammerAgent with a new Claude SDK
    session, drives one task to completion, then re-launches for the next
    task. The loop exits when there are no more tasks or the agent reports
    all work is done.

    Args:
        name: Programmer name (e.g., "Alice Chen").
        github_repo: GitHub repository in OWNER/REPO format.
        clone_path: Path to the main git clone.
        project: Project name.
        epic: GitHub issue number of the epic to work on.
        verbosity: Output verbosity level.
        resume: If True, automatically resume existing session (first run only).
        new_session: If True, always start a new session (first run only).
        max_iterations: Maximum loop iterations per task (safety limit).
        task_delay: Seconds to wait between tasks.
    """
    console = Console()
    first_run = True
    task_number = 0

    while True:
        task_number += 1

        if not first_run:
            console.print(
                f"\n[bold cyan]--- Starting task run #{task_number} ---[/bold cyan]\n"
            )

        agent = ProgrammerAgent(
            name=name,
            github_repo=github_repo,
            clone_path=clone_path,
            project=project,
            epic=epic,
            resume=resume if first_run else False,
            new_session=new_session if first_run else True,
            verbosity=verbosity,
            max_iterations=max_iterations,
        )

        await agent.run()

        exit_reason = agent.exit_reason
        first_run = False

        if exit_reason in ("all_done", "success"):
            break

        if exit_reason in ("task_complete", "no_tasks"):
            console.print(
                f"\n[dim]Waiting {task_delay}s before picking up next task...[/dim]"
            )
            try:
                await asyncio.sleep(task_delay)
            except (KeyboardInterrupt, asyncio.CancelledError):
                console.print("\n[yellow]Interrupted during delay. Exiting.[/yellow]")
                sys.exit(0)
            continue

        # Unknown exit reason or error — stop
        console.print(
            f"[yellow]Agent exited with reason: {exit_reason}. Stopping.[/yellow]"
        )
        break
