"""Reviewer agent that reviews PRs and merges approved code.

Each agent run drives a single PR review to completion (claim -> review ->
wait for feedback if needed -> re-review -> approve), then exits. A Python
outer loop in run_reviewer() manages a persistent TUI across multiple
review sessions.
"""

import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import ResultMessage

from softfoundry.agents.base import Agent, AgentConfig
from softfoundry.agents.prompts import (
    orchestrator_mcp_tools_prompt,
    project_info_prompt,
)
from softfoundry.mcp import create_orchestrator_server
from softfoundry.tui import SoftFoundryApp
from softfoundry.utils.github import LABEL_COLORS
from softfoundry.utils.status import sanitize_name

AGENT_TYPE = "reviewer"
POLL_INTERVAL = 30  # seconds to wait when polling for feedback
DEFAULT_MAX_ITERATIONS = 100
DEFAULT_TASK_DELAY = 60

# Exit signals the agent can output
EXIT_REVIEW_COMPLETE = "exit:review_complete"
EXIT_NO_PRS = "exit:no_prs"
EXIT_ALL_DONE = "exit:all_done"
EXIT_SIGNALS = (EXIT_REVIEW_COMPLETE, EXIT_NO_PRS, EXIT_ALL_DONE)


class ReviewerAgent(Agent):
    """Reviewer agent that drives a single PR review to completion.

    This agent:
    1. Claims an unreviewed PR from the epic
    2. Reviews the code (approve or request changes)
    3. If changes requested, waits for author to address feedback, then re-reviews
    4. Once approved, updates memory and exits

    The outer loop in run_reviewer() handles re-launching for new PRs.
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
        """Initialize the reviewer agent.

        Args:
            name: Reviewer name (e.g., "Rachel Review").
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
        self.exit_reason: str | None = None

        # Determine working directory (only set if path exists)
        cwd = self.clone_path if Path(self.clone_path).exists() else None

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
                "Write",
                "Glob",
                "Bash",
                "Grep",
                # Epic/Issue tools
                "mcp__orchestrator__get_epic_status",
                "mcp__orchestrator__get_sub_issue",
                # PR tools
                "mcp__orchestrator__get_pr_status",
                "mcp__orchestrator__list_prs_for_review",
                "mcp__orchestrator__list_my_reviews",
                "mcp__orchestrator__claim_pr_review",
                "mcp__orchestrator__request_changes",
                "mcp__orchestrator__approve_pr",
                "mcp__orchestrator__get_pr_feedback",
                "mcp__orchestrator__get_pr_diff",
                # Comment tools
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
        """Generate the system prompt for the reviewer agent."""
        project_info = project_info_prompt(
            github_repo=self.github_repo,
            epic=self.epic,
            clone_path=self.clone_path,
            status_path=self._status_path,
        )
        return f"""You are {self.name}, a code reviewer for the {self.project} project.

{project_info}
Your reviewer label: reviewer:{self.name_slug}

{orchestrator_mcp_tools_prompt()}

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

```bash
cat > {self._status_path} << 'EOF'
{{
    "agent_type":"reviewer",
    "name":"{self.name}",
    "project":"{self.project}",
    "status":"working",
    "details":"...",
    "current_pr":N,
    "last_update":"$(date -Iseconds)",
    "pid":{os.getpid()}
}}
EOF
```
Status values: starting, idle, working, exited:success

## Workflow

### 1. Find a PR

- First use the `mcp__orchestrator__list_my_reviews` tool to check for PRs you
  previously reviewed that are not merged yet.
- If you have any PRs assigned to you, focus on it and don't claim another PR.
- If no PRs assigned to you, claim a new PR to review using the
  `mcp__orchestrator__claim_pr_review` tool.
- Log your activity with the `mcp__orchestrator__log_activity` tool.
- Update the "current_pr" field of your status file.

If no PRs: check epic status:
- If all sub-issues closed and no open PRs output `exit:all_done`.
- Otherwise `exit:no_prs`.

### 2. Review

- Check `get_pr_status` — if `has_conflicts`, request changes asking author to rebase, then wait (step 5).
- Get diff with `get_pr_diff`.
- Check linked issue for context ("Closes #X" in PR body).
- Checkout branch locally: `git fetch origin && git checkout origin/BRANCH_NAME`
- Read code and understand changes.

### 3. Submit Review

**Criteria:** correctness, bugs/edge cases, code quality, style, tests.

- Keep review feedback concise and actionable.
- Only mention issues that must be addressed. Do not include strengths, praise,
  celebrations, or emojis.
- Only add inline comments for specific code issues that need fixes.
- If the PR looks good, approve it with the `approve_pr` tool and use exactly
  `Approved.` as the comment.
- If the PR needs changes, request changes with the `request_changes` tool and
  include only required fixes.
- Log the activity using the `mcp__orchestrator__log_activity` tool.

### 4. Wait and Re-Review

Check `mcp__orchestrator__get_pr_status`:

- Merged: go to step 6
- `has_feedback=False` after you requested changes: author addressed feedback, re-review
  (back to step 2)
- `is_approved=True`: wait for programmer to merge.
- Otherwise: wait and check again

### 6. Exit

1. Update memory file with review observations (PR number, code patterns, conventions).
   Organized by topic.
2. Set status to `exited:success`.
3. Output exit signal:
   - `exit:review_complete` — PR reviewed and approved/merged
   - `exit:no_prs` — no PRs available
   - `exit:all_done` — all epic work complete
"""

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()
        has_memory = self.memory_path and self.memory_path.exists()

        parts = [
            f"Start reviewing PRs for the {self.project} project.",
            "",
            f"GitHub repo: {self.github_repo}",
            f"Clone path: {self.clone_path}",
            f"Epic: #{self.epic}",
        ]

        if resume_context:
            parts.append("")
            parts.append(resume_context)
        else:
            parts.append("")
            parts.append(
                "First, check if you have any PRs already assigned to you "
                f'(`list_my_reviews(reviewer_name="{self.name}")`). '
                "If any need re-review (author addressed feedback), handle those first."
            )
            parts.append("")
            parts.append(
                "If you have no assigned PRs, find a new PR to review from the epic."
            )

        if not has_memory:
            parts.append("")
            parts.append(
                "This is your first session. "
                f'Create your reviewer label: `create_label(name="reviewer:{self.name_slug}", '
                f'color="{LABEL_COLORS["reviewer"]}", description="")`'
            )
            parts.append(
                f"Then log your start: `log_activity(epic_number={self.epic}, "
                f'agent_name="{self.name}", agent_type="reviewer", '
                f'event_type="started", message="Started and ready to review", '
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

        pr_num = existing_status.get("current_pr")
        if pr_num:
            return f"""IMPORTANT: You previously crashed or were interrupted.
Your last status was: {status}
You were reviewing PR #{pr_num}.
Details: {existing_status.get("details", "N/A")}

Check the current state of PR #{pr_num} and continue from where you left off."""

        return ""

    def is_complete(self, result: ResultMessage) -> bool:
        """Check if the reviewer has finished its current PR."""
        text = (result.result or "").lower()
        for signal in EXIT_SIGNALS:
            if signal in text:
                self.exit_reason = signal.split(":")[1]
                return True
        # Backward compatibility
        if "exited:success" in text:
            self.exit_reason = "success"
            return True
        return False

    def get_continuation_prompt(self) -> str:
        """Return the prompt to keep the agent reviewing."""
        return (
            "Continue reviewing. "
            "Check PR status, re-review if feedback was addressed, "
            "or wait for the PR to be merged."
        )

    # ─────────────────────────────────────────────────────────────────────────
    # OPTIONAL OVERRIDES
    # ─────────────────────────────────────────────────────────────────────────

    def get_idle_interval(self) -> int | None:
        """Wait 30 seconds if idle (waiting for feedback to be addressed)."""
        current_status = self.read_status()
        if current_status:
            status = current_status.get("status", "")
            if status == "idle":
                return POLL_INTERVAL
        return None

    def on_complete(self) -> None:
        """Handle completion with exit-reason-specific messaging."""
        reason = self.exit_reason or "success"

        if reason == "review_complete":
            self.update_status("exited:success", "PR review completed")
            if self._app:
                self._app.add_lifecycle_message("PR review completed!", "success")
        elif reason == "no_prs":
            self.update_status("exited:success", "No PRs available to review")
            if self._app:
                self._app.add_lifecycle_message(
                    "No PRs available to review.", "warning"
                )
        elif reason == "all_done":
            self.update_status("exited:success", "All epic work completed")
            if self._app:
                self._app.add_lifecycle_message(
                    "All PRs reviewed! Epic complete.", "success"
                )
        else:
            super().on_complete()


# ─────────────────────────────────────────────────────────────────────────────
# OUTER LOOP
# ─────────────────────────────────────────────────────────────────────────────


async def run_reviewer(
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
    """Run the reviewer agent in a loop, one PR per session.

    Creates a single persistent TUI that stays alive across multiple review
    sessions. Each iteration creates a fresh ReviewerAgent with a new
    Claude SDK session, while the TUI preserves message history.

    Args:
        name: Reviewer name (e.g., "Rachel Review").
        github_repo: GitHub repository in OWNER/REPO format.
        clone_path: Path to the main git clone.
        project: Project name.
        epic: GitHub issue number of the epic to work on.
        verbosity: Output verbosity level.
        resume: If True, automatically resume existing session (first run only).
        new_session: If True, always start a new session (first run only).
        max_iterations: Maximum loop iterations per PR (safety limit).
        task_delay: Seconds to wait between review runs.
        print_prompts_and_exit: If True, print prompts and exit without running.
    """
    if print_prompts_and_exit:
        agent = ReviewerAgent(
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

    async def _multi_session_worker(app: SoftFoundryApp) -> None:
        """Multi-session worker coroutine for persistent TUI.

        Runs inside the Textual event loop as an async worker.
        Creates fresh agents for each review.
        """
        run_number = 0

        while True:
            run_number += 1

            if run_number > 1:
                app.add_session_separator(f"Review #{run_number}")

            agent = ReviewerAgent(
                name=name,
                github_repo=github_repo,
                clone_path=clone_path,
                project=project,
                epic=epic,
                resume=resume if run_number == 1 else False,
                new_session=new_session if run_number == 1 else True,
                verbosity=verbosity,
                max_iterations=max_iterations,
            )

            try:
                await agent.run_session(app)
            except KeyboardInterrupt:
                app.add_lifecycle_message(
                    "Interrupted. Press Ctrl+D to exit.", "warning"
                )
                app.enable_input()
                return
            except Exception as e:
                app.add_lifecycle_message(f"Agent error: {e}", "error")
                app.add_lifecycle_message("Press Ctrl+D to exit.", "info")
                app.enable_input()
                return

            exit_reason = agent.exit_reason

            if exit_reason in ("all_done", "success"):
                app.add_lifecycle_message(
                    "All reviews completed. Press Ctrl+D to exit.", "success"
                )
                app.enable_input()
                return  # TUI stays open for user to review

            if exit_reason in ("review_complete", "no_prs"):
                # Show countdown in TUI
                app.add_lifecycle_message(f"Next review in {task_delay}s...", "info")
                app.update_status("idle")
                try:
                    await asyncio.sleep(task_delay)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    app.add_lifecycle_message(
                        "Interrupted. Press Ctrl+D to exit.", "warning"
                    )
                    app.enable_input()
                    return
                continue

            # Unknown exit reason — stop
            app.add_lifecycle_message(
                f"Agent exited ({exit_reason}). Press Ctrl+D to exit.", "warning"
            )
            app.enable_input()
            return

    # Create the persistent TUI app
    app = SoftFoundryApp(
        agent_type=AGENT_TYPE,
        agent_name=name,
        project=project,
        epic_number=epic,
        on_input=lambda text: None,  # Rewired per-session by _attach_tui
        agent_coroutine=lambda: _multi_session_worker(app),
    )
    app.verbosity = verbosity

    try:
        await app.run_async()
    except KeyboardInterrupt:
        sys.exit(0)
