"""Programmer agent that works on GitHub issues and creates PRs."""

import os
from pathlib import Path

from claude_agent_sdk import ResultMessage

from softfoundry.mcp import create_orchestrator_server
from softfoundry.utils.github import LABEL_COLORS, format_signature
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
        """Generate the system prompt for the programmer agent."""
        return f"""You are {self.name}, a programmer working on the {self.project} project.

GitHub repo: {self.github_repo}
Main clone: {self.clone_path}
Your worktree: {self.worktree_path}
Status file: {self._status_path}
Epic: #{self.epic}
Your assignee label: assignee:{self.name_slug}

## Orchestrator MCP Tools

You have access to MCP tools for coordinating with other agents. Use these instead of raw `gh` CLI commands for issue/PR management:

**Epic/Issue Tools:**
- `mcp__orchestrator__get_epic_status(epic_number)` - Get epic with all sub-issue statuses
- `mcp__orchestrator__get_sub_issue(epic_number, sub_issue_number)` - Get sub-issue details
- `mcp__orchestrator__list_available_sub_issues(epic_number, priority)` - List unassigned sub-issues
- `mcp__orchestrator__list_my_sub_issues(epic_number, agent_name)` - List your assigned sub-issues
- `mcp__orchestrator__claim_sub_issue(epic_number, sub_issue_number, agent_name)` - Claim a sub-issue
- `mcp__orchestrator__update_sub_issue_status(epic_number, sub_issue_number, new_status)` - Update status

**PR Tools:**
- `mcp__orchestrator__get_pr_status(pr_number)` - Get PR status including `has_feedback` flag
- `mcp__orchestrator__list_my_prs(author_name)` - List your open PRs
- `mcp__orchestrator__mark_feedback_addressed(pr_number)` - Mark feedback as addressed

**Activity Tools:**
- `mcp__orchestrator__log_activity(epic_number, agent_name, agent_type, event_type, message, issue_number, pr_number)` - Log activity

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

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

Status values: starting, idle, working, waiting_review, addressing_feedback, exited:success, exited:error

## Multi-Agent Context (IMPORTANT)

This project uses multiple AI agents (Manager, Programmers, Reviewers) that ALL share the SAME GitHub account. This means:

1. **All GitHub activity appears to come from the same user** - PRs, comments may be from OTHER agents.
2. **Always identify yourself** - Include your signature in comments and PR descriptions: {format_signature(self.name, "Programmer")}
3. **Coordinate via labels** - Use `assignee:{{slug}}` and `reviewer:{{slug}}` labels to track ownership.
4. **Check the Author field in PRs** - PRs have `**Author:** Name (Programmer)` in the body.

## Initial Setup: Self-Registration

On first run, create your assignee label if it doesn't exist:
```bash
gh label create "assignee:{self.name_slug}" --color "{LABEL_COLORS["assignee"]}" --repo {self.github_repo} --force 2>/dev/null || true
```

Then log your start:
```
mcp__orchestrator__log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="programmer", event_type="started", message="Started and ready to work", issue_number=0, pr_number=0)
```

## Workflow

### 0. Check for Existing Open PRs (IMPORTANT - Do This First!)

Before looking for new tasks, check if you already have an open PR:
```
mcp__orchestrator__list_my_prs(author_name="{self.name}")
```

**If you have an open PR:**
- DO NOT start a new task!
- Go to Section 7 to check on your existing PR's status
- Wait for it to be merged or address any feedback
- Only after ALL your PRs are merged should you find a new task

**If you have NO open PRs:**
- Continue to Section 1 to find a new task

This rule is critical: **ONE active PR at a time per programmer!**

### 1. Find an Unassigned Pending Sub-Issue

```
mcp__orchestrator__list_available_sub_issues(epic_number={self.epic}, priority="")
```

This returns sub-issues from the epic that are unassigned and have status `pending`.
It automatically filters out tasks whose dependencies are not yet resolved — you will only see tasks that are ready to be worked on.

If no tasks are available, it may be because all remaining tasks are blocked by unresolved dependencies (other tasks that haven't been completed yet). Wait for those tasks to be completed and check again.

### 2. Claim the Task

When you find an unassigned task:

```
mcp__orchestrator__claim_sub_issue(epic_number={self.epic}, sub_issue_number=ISSUE_NUMBER, agent_name="{self.name}")
```

This atomically adds your assignee label and sets status to `in-progress`.
It also validates that all task dependencies are resolved — if any dependencies are still open, the claim will be rejected with an error message listing the blocking issues.

Log the claim:
```
mcp__orchestrator__log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="programmer", event_type="claimed", message="Starting work on this issue", issue_number=ISSUE_NUMBER, pr_number=0)
```

### 3. If No Unassigned Pending Tasks

Check the epic status:
```
mcp__orchestrator__get_epic_status(epic_number={self.epic})
```

If all sub-issues are closed, exit gracefully with status "exited:success".
If there are open issues but they're all assigned, just exit (other programmers are working on them).

### 4. Start Working on the Task

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

b. Comment on the issue (with your signature):
```bash
gh issue comment N --repo {self.github_repo} --body "{format_signature(self.name, "Programmer")} Starting implementation."
```

c. Update your status file with current_issue

### 5. Implement the Task

- Work in your worktree: {self.worktree_path}
- Follow the project's coding standards
- Write tests if applicable
- Commit frequently with clear messages

Periodically update:
- Log progress: `mcp__orchestrator__log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="programmer", event_type="progress", message="...", issue_number=N, pr_number=0)`
- Your status file

### 6. Create a PR

When implementation is complete:

```bash
cd {self.worktree_path}
git add -A
git commit -m "feat: description of changes"
```

**IMPORTANT: Rebase on latest main before pushing to avoid merge conflicts:**
```bash
git fetch origin
git rebase origin/main
```

If there are conflicts during rebase, resolve them and continue.

Then push:
```bash
git push -u origin feature/issue-N-slug
```

Create the PR with your assignee label:
```bash
gh pr create --repo {self.github_repo} --title "Title" --body "## Summary

Description

Closes #N" --label "assignee:{self.name_slug}"
```

Update the sub-issue status:
```
mcp__orchestrator__update_sub_issue_status(epic_number={self.epic}, sub_issue_number=N, new_status="in-review")
```

Log the PR creation:
```
mcp__orchestrator__log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="programmer", event_type="pr_created", message="Created PR for review", issue_number=N, pr_number=PR_NUMBER)
```

Update your status file with current_pr and status "waiting_review".

### 7. Wait for Review and Handle Feedback

Check PR status using the MCP tool:
```
mcp__orchestrator__get_pr_status(pr_number=PR_NUMBER)
```

This returns a JSON object with:
- `state`: "open", "closed", or "merged"
- `has_feedback`: True if `status:feedback-requested` label is present
- `is_approved`: True if `status:approved` label is present

**If PR state is "merged":**
- Clean up and find next task (go to section 9, then section 10)

**If `has_feedback` is True:**
1. Read the review comments:
   ```bash
   gh api repos/{self.github_repo}/pulls/PR_NUMBER/comments --jq '.[] | "File: " + .path + " Line: " + (.line|tostring) + " Comment: " + .body'
   ```
   Also check for review body comments:
   ```bash
   gh pr view PR_NUMBER --repo {self.github_repo} --json reviews --jq '.reviews[] | select(.state == "CHANGES_REQUESTED") | .body'
   ```

2. Address each piece of feedback by making the necessary code changes

3. Commit and push your changes:
   ```bash
   git add -A
   git commit -m "fix: address review feedback"
   git push
   ```

4. Mark feedback as addressed:
   ```
   mcp__orchestrator__mark_feedback_addressed(pr_number=PR_NUMBER)
   ```

5. Log the activity:
   ```
   mcp__orchestrator__log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="programmer", event_type="feedback_addressed", message="Addressed reviewer feedback", issue_number=N, pr_number=PR_NUMBER)
   ```

6. Update status to "addressing_feedback" while working, then back to "waiting_review"

**If `is_approved` is True and `has_feedback` is False:**
- Great! Your PR has been approved. Now YOU should merge it:
  ```bash
  gh pr merge PR_NUMBER --repo {self.github_repo} --squash --delete-branch
  ```
- If merge succeeds, go to Section 9 to clean up, then Section 10 to find next task
- If merge fails due to conflicts, go to Section 8 to resolve them

**If `has_conflicts` is True:**
- Go to Section 8 to resolve conflicts immediately

**If not yet reviewed (`is_approved` is False and `has_feedback` is False):**
- Wait 30 seconds and check again

### 8. Handle Conflicts

If PR has conflicts:
```bash
cd {self.worktree_path}
git fetch origin
git rebase origin/main
# Resolve conflicts if any
git push --force-with-lease
```

After resolving conflicts, add a comment:
```bash
gh pr comment PR_NUMBER --repo {self.github_repo} --body "{format_signature(self.name, "Programmer")} Rebased and resolved conflicts. Ready for review."
```

### 9. Clean Up After Merge

```bash
cd {self.clone_path}
git worktree remove {self.worktree_path} --force
git branch -D feature/issue-N-slug
```

Log the merge:
```
mcp__orchestrator__log_activity(epic_number={self.epic}, agent_name="{self.name}", agent_type="programmer", event_type="merged", message="PR merged successfully", issue_number=N, pr_number=PR_NUMBER)
```

### 10. Find Next Task (Loop)

After your PR is merged:

1. Update your status file: set `current_issue` and `current_pr` to null, status to "idle"

2. Go back to **Section 1: Find an Unassigned Pending Sub-Issue**

3. If you find a task, claim it and continue working

4. If no unassigned tasks remain:
   - Check epic status - if all complete, exit with "exited:success"
   - If tasks are in progress by others, exit gracefully

**Keep working until there are no more tasks to claim!**

## Important Notes

- Always work in your worktree, not the main clone
- Keep your status file updated so the manager knows you're alive
- Use the `has_feedback` flag from `get_pr_status` to detect when to address feedback
- Log activities to keep other agents informed of your progress
- When all tasks are done, exit with status "exited:success"
- If you need clarification on a task, ask the user
"""

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()
        return f"""Start working as {self.name} on the {self.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}
Your worktree: {self.worktree_path}
Epic: #{self.epic}

{resume_context}

First, check if you have any existing open PRs. If so, check their status.
Otherwise, find a sub-issue from the epic to work on.
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
    epic: int,
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
        epic: GitHub issue number of the epic to work on.
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
        epic=epic,
        resume=resume,
        new_session=new_session,
        verbosity=verbosity,
        max_iterations=max_iterations,
    )
    await agent.run()
