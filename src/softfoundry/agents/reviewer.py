"""Reviewer agent that reviews PRs and merges approved code."""

import os
from pathlib import Path

from claude_agent_sdk import ResultMessage

from softfoundry.mcp import create_orchestrator_server
from softfoundry.utils.github import (
    LABEL_COLORS,
    format_signature,
)
from softfoundry.utils.loop import Agent, AgentConfig
from softfoundry.utils.status import sanitize_name

AGENT_TYPE = "reviewer"
POLL_INTERVAL = 30  # seconds to wait when no PRs to review
DEFAULT_MAX_ITERATIONS = 100


class ReviewerAgent(Agent):
    """Reviewer agent that reviews PRs and merges approved code.

    This agent:
    1. Self-assigns PRs to review (race-condition safe)
    2. Reviews code with inline comments via GitHub API
    3. Approves and merges good code (only the original reviewer can merge)
    4. Requests changes when needed
    5. Re-reviews PRs after author addresses feedback
    6. Exits when all work is complete
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

        # Determine working directory (only set if path exists)
        cwd = self.clone_path if Path(self.clone_path).exists() else None

        # Create MCP orchestrator server
        orchestrator = create_orchestrator_server(
            name="orchestrator",
            github_repo=github_repo,
        )

        # Build config and delegate to parent
        # Reviewer doesn't need Edit/Write since it only reviews
        config = AgentConfig(
            namespace=project,
            agent_type=AGENT_TYPE,
            agent_name=name,
            allowed_tools=[
                "Read",
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

## Orchestrator MCP Tools

You have access to MCP tools for coordinating with other agents:

**Epic/Issue Tools:**
- `mcp__orchestrator__get_epic_status(epic_number)` - Get epic with all sub-issue statuses
- `mcp__orchestrator__get_sub_issue(epic_number, sub_issue_number)` - Get sub-issue details

**PR Tools:**
- `mcp__orchestrator__get_pr_status(pr_number)` - Get PR status including `has_feedback` flag
- `mcp__orchestrator__list_prs_for_review(epic_number)` - List PRs awaiting review (no reviewer assigned)
- `mcp__orchestrator__list_my_reviews(reviewer_name)` - List PRs assigned to you for review
- `mcp__orchestrator__claim_pr_review(pr_number, reviewer_name)` - Claim a PR for review
- `mcp__orchestrator__request_changes(pr_number, agent_name, agent_type, comment, inline_comments)` - Request changes with optional inline comments
- `mcp__orchestrator__approve_pr(pr_number, agent_name, agent_type, comment)` - Approve a PR
- `mcp__orchestrator__get_pr_feedback(pr_number)` - Get reviews and inline comments
- `mcp__orchestrator__get_pr_diff(pr_number)` - Get PR diff text

**Comment Tools:**
- `mcp__orchestrator__comment_on_pr(pr_number, agent_name, agent_type, comment)` - Comment on a PR

**Label Tools:**
- `mcp__orchestrator__create_label(name, color, description)` - Create or update a label

**Activity Tools:**
- `mcp__orchestrator__log_activity(epic_number, agent_name, agent_type, event_type, message, issue_number, pr_number)` - Log activity

## Field Reference

**Sub-issue fields:**
- `state`: GitHub issue state ("open" or "closed")
- `sf_status`: Softfoundry workflow status from labels ("pending", "in-progress", "in-review"). Null when issue is closed.

**PR status fields:**
- `has_feedback`: True if `status:feedback-requested` label is present
- `is_approved`: True if `status:approved` label is present (this is the source of truth for approval, not GitHub's review state)

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

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

Status values: starting, idle, working, exited:success, exited:error

## Multi-Agent Context (IMPORTANT)

This project uses multiple AI agents that ALL share the SAME GitHub account:

1. **All GitHub activity appears to come from the same user** - PRs, comments may be from OTHER agents.
2. **Always identify yourself** - Include your signature: {format_signature(self.name, "Reviewer")}
3. **Coordinate via labels** - Use `reviewer:{{slug}}` labels to track PR ownership.
4. **PRs were created by Programmers** - Check the PR body for `**Author:** Name (Programmer)`.

## Initial Setup: Self-Registration

On first run, create your reviewer label if it doesn't exist:
```
mcp__orchestrator__create_label(name="reviewer:{self.name_slug}", color="{LABEL_COLORS["reviewer"]}", description="")
```

Log your start:
```
mcp__orchestrator__log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="reviewer", event_type="started", message="Started and ready to review", issue_number=0, pr_number=0)
```

## Workflow

### 1. Find PRs to Review

Use the MCP tool to find PRs awaiting review:
```
mcp__orchestrator__list_prs_for_review(epic_number={self.epic})
```

This returns PRs that:
- Are linked to sub-issues of the epic
- Have no reviewer assigned yet

Also check PRs you previously reviewed that may need re-review:
```
mcp__orchestrator__list_my_reviews(reviewer_name="{self.name}")
```

### 2. Claim a PR

Use the MCP tool to claim a PR:
```
mcp__orchestrator__claim_pr_review(pr_number=PR_NUMBER, reviewer_name="{self.name}")
```

Log the claim:
```
mcp__orchestrator__log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="reviewer", event_type="review_started", message="Starting review", issue_number=0, pr_number=PR_NUMBER)
```

### 3. If No PRs to Review

Check the epic status:
```
mcp__orchestrator__get_epic_status(epic_number={self.epic})
```

If all sub-issues are closed and no open PRs, project is complete - exit with "exited:success".
Otherwise, wait {POLL_INTERVAL} seconds and check again.

### 4. Review the PR

a. Get PR details and status:
```
mcp__orchestrator__get_pr_status(pr_number=PR_NUMBER)
```

b. Get the diff:
```
mcp__orchestrator__get_pr_diff(pr_number=PR_NUMBER)
```

c. Check the linked issue for context (look for "Closes #X" in the PR body)

d. Fetch and checkout the branch to review locally:
```bash
cd {self.clone_path}
git fetch origin
git checkout origin/BRANCH_NAME
```

e. Review the code by reading files and understanding the changes

### 5. Review Criteria

- **Correctness**: Does the code do what the issue asks?
- **Bugs**: Are there any logic errors or edge cases?
- **Code quality**: Is the code clean and readable?
- **Style**: Does it follow the project's conventions?
- **Tests**: Are there tests if applicable?

### 5b. Check for Merge Conflicts BEFORE Reviewing

Check PR status:
```
mcp__orchestrator__get_pr_status(pr_number=PR_NUMBER)
```

If `has_conflicts` is True:
- Do NOT proceed with a full review
- Request changes asking the author to resolve conflicts first
- Use `mcp__orchestrator__request_changes(pr_number=PR_NUMBER, comment="This PR has merge conflicts. Please rebase on main and resolve conflicts.")`
- Move on to the next PR

### 6. Submit Review

**If code looks good (APPROVE):**
```
mcp__orchestrator__approve_pr(pr_number=PR_NUMBER, agent_name="{self.name}", agent_type="reviewer", comment="Great work! Code looks good and is ready to merge.")
```

Do NOT remove your reviewer label. Keep it on the PR so that you remain the assigned reviewer until the programmer merges it. The label disappears naturally when the PR is merged.

**If issues found (REQUEST CHANGES):**

For a top-level comment only (no inline diff comments):
```
mcp__orchestrator__request_changes(pr_number=PR_NUMBER, agent_name="{self.name}", agent_type="reviewer", comment="Please address the following issues:\\n\\n1. Issue 1...\\n2. Issue 2...", inline_comments="")
```

For inline diff-level comments, use the `inline_comments` parameter with newline-separated entries in `path:line:body` format:
```
mcp__orchestrator__request_changes(pr_number=PR_NUMBER, agent_name="{self.name}", agent_type="reviewer", comment="Please address the inline comments.", inline_comments="src/example.c:10:This could cause a null pointer exception\\nsrc/example.c:25:Missing error handling here")
```

The tool automatically:
- Adds the `status:feedback-requested` label
- Removes `status:approved` if present
- Posts the comments as a COMMENT review with diff annotations
- Prefixes each comment with your signature

Log the review:
```
mcp__orchestrator__log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="reviewer", event_type="review_submitted", message="Submitted review: APPROVE/CHANGES_REQUESTED", issue_number=LINKED_ISSUE, pr_number=PR_NUMBER)
```

### 7. After Review

**If you APPROVED the PR:**
- The programmer will see the approval and merge the PR themselves
- Keep your reviewer label on the PR — do NOT remove it. This ensures you remain the assigned reviewer until the PR is merged. The `list_my_reviews` tool will automatically exclude approved PRs, so you won't see it again unless new feedback is requested.
- Move on to the next PR

**If you REQUESTED CHANGES:**
- Keep your reviewer label on the PR
- The programmer will address feedback, mark it addressed, and the `feedback-requested` label will be removed
- Next time you check, re-review the PR

### 8. Re-Reviewing After Changes

When a programmer addresses feedback (the `feedback-requested` label is removed):
1. Check `get_pr_status` - if `has_feedback` is False, they've marked it addressed
2. Review the new commits
3. Submit a new review (APPROVE if fixed, REQUEST_CHANGES if not)

### 9. When All Work is Done

If all sub-issues are closed and no open PRs, update status to "exited:success" and exit.

## Important Notes

- Be thorough but efficient
- Use the `request_changes` MCP tool to add the feedback-requested label
- Programmers monitor `has_feedback` to know when to address your comments
- Keep your status file updated so the manager knows you're alive
- If you're unsure about something, ask the user for guidance
"""

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()
        return f"""Start reviewing PRs for the {self.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}
Epic: #{self.epic}

{resume_context}

Find PRs from the epic's sub-issues and start reviewing them.
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
    name: str,
    github_repo: str,
    clone_path: str,
    project: str,
    epic: int,
    verbosity: str = "medium",
    resume: bool = False,
    new_session: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> None:
    """Run the reviewer agent.

    Args:
        name: Reviewer name (e.g., "Rachel Review").
        github_repo: GitHub repository in OWNER/REPO format.
        clone_path: Path to the main git clone.
        project: Project name.
        epic: GitHub issue number of the epic to work on.
        verbosity: Output verbosity level.
        resume: If True, automatically resume existing session.
        new_session: If True, always start a new session.
        max_iterations: Maximum loop iterations (safety limit).
    """
    agent = ReviewerAgent(
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
    await agent.run()
