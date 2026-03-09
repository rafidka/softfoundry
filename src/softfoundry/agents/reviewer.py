"""Reviewer agent that reviews PRs and merges approved code.

Each agent run drives a single PR review to completion (claim -> review ->
wait for feedback if needed -> re-review -> approve), then exits. A Python
outer loop in run_reviewer() handles re-launching the agent for the next PR
with a fresh session.
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
        )
        super().__init__(config)

    # ─────────────────────────────────────────────────────────────────────────
    # ABSTRACT METHOD IMPLEMENTATIONS
    # ─────────────────────────────────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        """Generate the system prompt for the reviewer agent."""
        return f"""You are {self.name}, a code reviewer for the {self.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}
Status file: {self._status_path}
Epic: #{self.epic}
Your reviewer label: reviewer:{self.name_slug}

## MCP Tools

You have MCP tools for coordinating with other agents. Use these instead of raw `gh` CLI commands:

**Epic/Issue Tools:**
- `get_epic_status(epic_number)` - Get epic with all sub-issue statuses
- `get_sub_issue(epic_number, sub_issue_number)` - Get sub-issue details

**PR Tools:**
- `get_pr_status(pr_number)` - Get PR status (`has_feedback`, `is_approved`, `has_conflicts`)
- `list_prs_for_review(epic_number)` - List unreviewed PRs linked to epic
- `list_my_reviews(reviewer_name)` - List PRs assigned to you for review
- `claim_pr_review(pr_number, reviewer_name)` - Claim a PR for review
- `request_changes(pr_number, agent_name, agent_type, comment, inline_comments)` - Request changes
- `approve_pr(pr_number, agent_name, agent_type, comment)` - Approve a PR
- `get_pr_feedback(pr_number)` - Get reviews and inline comments
- `get_pr_diff(pr_number)` - Get PR diff text

**Other Tools:**
- `comment_on_pr(pr_number, agent_name, agent_type, comment)` - Comment on PR
- `create_label(name, color, description)` - Create or update a label
- `log_activity(epic_number, agent_name, agent_type, event_type, message, issue_number, pr_number)` - Log activity

All MCP tools are prefixed with `mcp__orchestrator__` when calling them.

## Field Reference

**Sub-issue fields:** `state` (open/closed), `sf_status` (pending/in-progress/in-review, null when closed), `assignee` (agent slug), `reviewer` (reviewer slug), `linked_pr` (PR number).

**PR status fields:** `has_feedback` (True if feedback-requested label present), `is_approved` (True if approved label present), `has_conflicts` (True if merge conflicts exist).

## Status File

Update your status file frequently:
```bash
cat > {self._status_path} << 'EOF'
{{
  "agent_type": "reviewer",
  "name": "{self.name}",
  "project": "{self.project}",
  "status": "working",
  "details": "Description of what you're doing",
  "current_pr": 5,
  "last_update": "$(date -Iseconds)",
  "pid": {os.getpid()}
}}
EOF
```

Status values: starting, idle, working, exited:success

## Multi-Agent Context

Multiple AI agents share the SAME GitHub account. Always identify yourself with your signature: {format_signature(self.name, "Reviewer")}
Coordinate via labels (`reviewer:{{slug}}`). PRs were created by Programmers -- check the PR body for `**Author:** Name (Programmer)`.

## Workflow

### 1. Find a PR to Review

First check for PRs you previously reviewed that may need re-review:
```
list_my_reviews(reviewer_name="{self.name}")
```
This returns PRs assigned to you. If any have `has_feedback` as False (author addressed your feedback), re-review them first.

Then check for new unreviewed PRs:
```
list_prs_for_review(epic_number={self.epic})
```

If no PRs are available, check the epic status. If all sub-issues are closed and no open PRs, exit with `exit:all_done`. Otherwise exit with `exit:no_prs`.

### 2. Claim a PR

```
claim_pr_review(pr_number=PR_NUMBER, reviewer_name="{self.name}")
```

Log the claim:
```
log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="reviewer", event_type="review_started", message="Starting review", issue_number=0, pr_number=PR_NUMBER)
```

Update your status file with `current_pr`.

### 3. Review the PR

a. Check for merge conflicts first:
```
get_pr_status(pr_number=PR_NUMBER)
```
If `has_conflicts` is True, request changes asking the author to rebase, then wait for them to fix it (go to step 5).

b. Get the diff:
```
get_pr_diff(pr_number=PR_NUMBER)
```

c. Check the linked issue for context (look for "Closes #X" in the PR body)

d. Fetch and checkout the branch to review locally:
```bash
cd {self.clone_path}
git fetch origin
git checkout origin/BRANCH_NAME
```

e. Review the code by reading files and understanding the changes

### 4. Submit Review

**Review criteria:** correctness, bugs/edge cases, code quality, style consistency, tests.

**If code looks good (APPROVE):**
```
approve_pr(pr_number=PR_NUMBER, agent_name="{self.name}", agent_type="reviewer", comment="Code looks good and is ready to merge.")
```
The programmer will merge the PR. Wait for it to be merged (go to step 5).

**If issues found (REQUEST CHANGES):**

For top-level comment only:
```
request_changes(pr_number=PR_NUMBER, agent_name="{self.name}", agent_type="reviewer", comment="Please address:\\n1. Issue...", inline_comments="")
```

For inline diff-level comments, use `inline_comments` with `path:line:body` format:
```
request_changes(pr_number=PR_NUMBER, agent_name="{self.name}", agent_type="reviewer", comment="Please address the inline comments.", inline_comments="src/example.c:10:Null pointer risk\\nsrc/example.c:25:Missing error handling")
```

Log the review:
```
log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="reviewer", event_type="review_submitted", message="Submitted review", issue_number=0, pr_number=PR_NUMBER)
```

### 5. Wait and Re-Review

Check PR status:
```
get_pr_status(pr_number=PR_NUMBER)
```

- **If PR is merged:** Go to step 6 (exit).
- **If `has_feedback` is False and you previously requested changes:** The author addressed your feedback. Re-review the changes (go back to step 3).
- **If `is_approved` is True:** The PR is approved, wait for the programmer to merge it.
- **Otherwise:** Wait and check again.

### 6. Update Memory and Exit

After the PR is approved or merged:

1. Update your memory file with what you reviewed (PR number, what the code did, review quality patterns, project conventions you noticed). Keep it organized by topic.

2. Update your status file to `exited:success`.

3. Output your exit signal as the very last thing in your response:
   - `exit:review_complete` -- PR reviewed and approved (or merged)
   - `exit:no_prs` -- No PRs available to review
   - `exit:all_done` -- All epic sub-issues are closed, no open PRs

## Important Notes

- Be thorough but efficient in reviews
- Stay assigned to the same PR until it's approved or merged -- do NOT abandon a review
- Use the `request_changes` MCP tool to add the feedback-requested label
- Keep your status file updated (the manager monitors heartbeats)
- One PR per session -- review it to completion, then exit
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
            self.printer.console.print("[bold green]PR review completed![/bold green]")
        elif reason == "no_prs":
            self.update_status("exited:success", "No PRs available to review")
            self.printer.console.print("[yellow]No PRs available to review.[/yellow]")
        elif reason == "all_done":
            self.update_status("exited:success", "All epic work completed")
            self.printer.console.print(
                "[bold green]All PRs reviewed! Epic complete.[/bold green]"
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
) -> None:
    """Run the reviewer agent in a loop, one PR per session.

    Each iteration creates a fresh ReviewerAgent with a new Claude SDK
    session, drives one PR review to completion, then re-launches for the
    next PR. The loop exits when there are no more PRs or the agent reports
    all work is done.

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
    """
    console = Console()
    first_run = True
    run_number = 0

    while True:
        run_number += 1

        if not first_run:
            console.print(
                f"\n[bold cyan]--- Starting review run #{run_number} ---[/bold cyan]\n"
            )

        agent = ReviewerAgent(
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

        if exit_reason in ("review_complete", "no_prs"):
            console.print(
                f"\n[dim]Waiting {task_delay}s before picking up next PR...[/dim]"
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
