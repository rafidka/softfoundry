"""Programmer agent that works on GitHub issues and creates PRs.

Each agent run drives a single issue to completion (claim -> implement ->
PR -> review -> merge), then exits. A Python outer loop in run_programmer()
manages a persistent TUI across multiple task sessions.
"""

import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import ResultMessage

from softfoundry.agents.base import Agent, AgentConfig
from softfoundry.agents.prompts import (
    project_info_prompt,
)
from softfoundry.mcp import create_orchestrator_server
from softfoundry.tui import AgentBridge, SoftFoundryApp
from softfoundry.utils.github import LABEL_COLORS
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
        dry_mode: bool = False,
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
            dry_mode: If True, skip session resolution and status file writes.
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
            dry_mode=dry_mode,
        )
        super().__init__(config)

    # ─────────────────────────────────────────────────────────────────────────
    # ABSTRACT METHOD IMPLEMENTATIONS
    # ─────────────────────────────────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        """Generate the system prompt for the programmer agent."""
        project_info = project_info_prompt(
            github_repo=self.github_repo,
            epic=self.epic,
            clone_path=self.clone_path,
            status_path=self._status_path,
        )
        return f"""You are {self.name}, a programmer working on the {self.project} project.

{project_info}

Your assignee label: assignee:{self.name_slug}

Use `mcp__orchestrator__*` tools for all GitHub mutating activities.

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

```bash
cat > {self._status_path} << 'EOF'
{{
    "agent_type":"programmer",
    "name":"{self.name}",
    "project":"{self.project}",
    "status":"working",
    "details":"...",
    "current_issue":N,
    "current_pr":null,
    "last_update":"$(date -Iseconds)",
    "pid":{os.getpid()}
}}
EOF
```

Status values: starting, idle, working, waiting_review, addressing_feedback, exited:success

## Workflow

### 1. Claim a Task

- Use mcp__orchestrator__list_available_sub_issues to find available tasks.
- Use mcp__orchestrator__claim_sub_issue to claim a task.
- Comment on the issue using the mcp__orchestrator__comment_on_issue tool.
- Log the claim with mcp__orchestrator__log_activity.

If no tasks available: check epic — if all closed, output `exit:all_done`. Otherwise `exit:no_tasks`.

### 2. Set Up Worktree

```bash
cd {self.clone_path} && git fetch origin
git worktree add {self.worktree_path} -b feature/issue-N-slug origin/main

# Or if worktree exists:
cd {self.worktree_path} && git fetch origin && git checkout -b feature/issue-N-slug origin/main
```

### 3. Implement

Work in `{self.worktree_path}`. Follow project standards, write tests if applicable,
commit frequently. Update status file periodically.

### 4. Create PR

```bash
cd {self.worktree_path}
git add -A && git commit -m "feat: description"
git fetch origin && git rebase origin/main
git push -u origin feature/issue-N-slug
```

Use `mcp__orchestrator__create_pr` with your assignee label, body containing "Closes
#N". Then `mcp__orchestrator__update_sub_issue_status` to "in-review". Set your status
in the status file to `waiting_review`.

### 5. Handle Review

Check with `mcp__orchestrator__get_pr_status`:

- **Merged**: go to step 7
- **`has_feedback`**: `mcp__orchestrator__get_pr_feedback`, fix issues, push, `mcp__orchestrator__mark_feedback_addressed`
- **`is_approved`**: `mcp__orchestrator__merge_pr` (use squash method). If conflicts, step 6
- **`has_conflicts`**: step 6
- **Not reviewed**: use bash to sleep for 1 minute and check again.

### 6. Handle Conflicts

- Use git to fetch the latest changes from the origin repository and rebase the worktree
  based on the main branch.
- If there are conflicts, resolve them. Then force push the
  changes to your feature branch.
- Use `mcp__orchestrator__comment_on_pr` to comment on the PR that the
  conflicts are resolved.
- Use `mcp__orchestrator__log_activity` to log the activity.
- Go back to step 5.

### 7. Clean Up

```bash
cd {self.clone_path}
git worktree remove {self.worktree_path} --force
git branch -D feature/issue-N-slug
```

### 8. On Exit

1. Update memory file with learnings (patterns, architecture, gotchas). Organized by topic.
2. Set status to `exited:success`.
3. Output exit signal as the last thing:
   - `exit:task_complete` — PR merged
   - `exit:no_tasks` — nothing to claim
   - `exit:all_done` — all epic sub-issues closed

## Rules

- Always work in your worktree, not the main clone
- Keep your status file updated (manager monitors heartbeats)
- One task per session: implement, review, merge, then exit
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
    print_prompts_and_exit: bool = False,
) -> None:
    """Run the programmer agent in a loop, one task per session.

    Creates a single persistent TUI that stays alive across multiple task
    sessions. Each iteration creates a fresh ProgrammerAgent with a new
    Claude SDK session, while the TUI preserves message history.

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
        print_prompts_and_exit: If True, print prompts and exit without running.
    """
    if print_prompts_and_exit:
        agent = ProgrammerAgent(
            name=name,
            github_repo=github_repo,
            clone_path=clone_path,
            project=project,
            epic=epic,
            dry_mode=True,
        )
        print("=== SYSTEM PROMPT ===\n")
        print(agent.get_system_prompt())
        print("\n=== INITIAL PROMPT ===\n")
        print(agent.get_initial_prompt())
        return

    # Create the first agent BEFORE the TUI starts (session resolution
    # may use bare input() for interactive prompts, which requires a
    # normal terminal — not the Textual TUI).
    first_agent = ProgrammerAgent(
        name=name,
        github_repo=github_repo,
        clone_path=clone_path,
        project=project,
        epic=epic,
        resume=resume,
        new_session=new_session,
        verbosity=verbosity,
        max_iterations=max_iterations,
    )

    async def _multi_session_worker(app: SoftFoundryApp, bridge: AgentBridge) -> None:
        """Multi-session worker coroutine for persistent TUI.

        Runs inside the Textual event loop as an async worker.
        Uses the pre-created first agent, then creates fresh agents
        for subsequent tasks.
        """
        task_number = 0
        agent = first_agent

        while True:
            task_number += 1

            if task_number > 1:
                bridge.show_session_separator(f"Task #{task_number}")
                agent = ProgrammerAgent(
                    name=name,
                    github_repo=github_repo,
                    clone_path=clone_path,
                    project=project,
                    epic=epic,
                    resume=False,
                    new_session=True,
                    verbosity=verbosity,
                    max_iterations=max_iterations,
                )

            try:
                await agent.run_session(app, bridge)
            except KeyboardInterrupt:
                bridge.show_lifecycle_message(
                    "Interrupted. Press Ctrl+D to exit.", "warning"
                )
                bridge.enable()
                return
            except Exception as e:
                bridge.show_lifecycle_message(f"Agent error: {e}", "error")
                bridge.show_lifecycle_message("Press Ctrl+D to exit.", "info")
                bridge.enable()
                return

            exit_reason = agent.exit_reason

            if exit_reason in ("all_done", "success"):
                bridge.show_lifecycle_message(
                    "All tasks completed. Press Ctrl+D to exit.", "success"
                )
                bridge.enable()
                return  # TUI stays open for user to review

            if exit_reason in ("task_complete", "no_tasks"):
                # Show countdown in TUI
                bridge.show_lifecycle_message(f"Next task in {task_delay}s...", "info")
                bridge.status = "idle"
                try:
                    await asyncio.sleep(task_delay)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    bridge.show_lifecycle_message(
                        "Interrupted. Press Ctrl+D to exit.", "warning"
                    )
                    bridge.enable()
                    return
                continue

            # Unknown exit reason — stop
            bridge.show_lifecycle_message(
                f"Agent exited ({exit_reason}). Press Ctrl+D to exit.", "warning"
            )
            bridge.enable()
            return

    # Create the persistent TUI app
    app = SoftFoundryApp(
        agent_type=AGENT_TYPE,
        agent_name=name,
        project=project,
        epic_number=epic,
        on_input=lambda text: None,  # Rewired per-session by _attach_tui
        agent_coroutine=lambda: _multi_session_worker(app, bridge),
    )
    bridge = AgentBridge(app=app)
    bridge._verbosity = verbosity

    try:
        await app.run_async()
    except KeyboardInterrupt:
        sys.exit(0)
