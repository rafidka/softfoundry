"""Reviewer agent that reviews PRs and merges approved code."""

import os
from pathlib import Path

from claude_agent_sdk import ResultMessage

from softfoundry.utils.github import (
    LABEL_COLORS,
    format_inline_signature,
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
        self.project = project

        # Determine working directory
        cwd = str(Path(clone_path).resolve()) if Path(clone_path).exists() else None

        # Build config and delegate to parent
        # Reviewer doesn't need Edit/Write since it only reviews
        config = AgentConfig(
            namespace=project,
            agent_type=AGENT_TYPE,
            agent_name=name,
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
        # Extract owner and repo for API calls
        owner, repo = self.github_repo.split("/")

        return f"""You are {self.name}, a code reviewer for the {self.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}
Status file: {self._status_path}
Your reviewer label: reviewer:{self.name_slug}

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

This project uses multiple AI agents (Manager, Programmers, Reviewers) that ALL share the SAME GitHub account. This means:

1. **All GitHub activity appears to come from the same user** - When you see issues, PRs, or comments, they may have been created by OTHER agents, not you.

2. **Do NOT be confused by "your own" activity** - If you see a PR or comment that you don't remember creating, it was likely created by another agent (a Programmer, another Reviewer, or the Manager).

3. **Always identify yourself** - Since GitHub can't distinguish between agents, include your signature:
   - For review comments: {format_signature(self.name, "Reviewer")}
   - For inline code comments: {format_inline_signature(self.name)}

4. **Coordinate via labels, not usernames** - Use `reviewer:{{slug}}` labels to track PR ownership. The GitHub author fields will show the same user for everyone, so IGNORE those and trust the labels.

5. **PRs were created by Programmers, not you** - Check the PR body for the **Author:** field to see which programmer created it (e.g., "**Author:** Alice Chen (Programmer)").

6. **Other reviewers may exist** - If you see a `reviewer:` label that's not yours, another Reviewer agent claimed that PR. Only review PRs you've claimed or that are unclaimed.

## Initial Setup: Self-Registration

On first run, create your reviewer label if it doesn't exist:
```bash
gh label create "reviewer:{self.name_slug}" --color "{LABEL_COLORS["reviewer"]}" --repo {self.github_repo} --force 2>/dev/null || true
```

## Workflow

### 1. Find PRs to Review

There are two types of PRs you should look for:

**Type A: PRs you previously reviewed that need re-review (PRIORITY)**
These are PRs with your reviewer label that had changes requested and may have new commits:
```bash
gh pr list --repo {self.github_repo} --state open --label "reviewer:{self.name_slug}" --json number,title,labels,updatedAt
```

Check if there are new commits since your last review. If yes, re-review these FIRST.

**Type B: Unassigned PRs needing initial review**
```bash
gh pr list --repo {self.github_repo} --state open --label "status:in-review" --json number,title,labels
```

From the results, find PRs that do NOT have any "reviewer:*" label.

### 2. Claim a PR (Race-Condition Safe)

When you find an unassigned PR to review:

**Step 1:** Add your reviewer label to the PR:
```bash
gh issue edit PR_NUMBER --repo {self.github_repo} --add-label "reviewer:{self.name_slug}"
```
(Note: PRs are issues in GitHub, so `gh issue edit` works on PRs)

**Step 2:** Wait briefly for concurrent claims:
```bash
sleep 0.2
```

**Step 3:** Re-fetch the PR to check for conflicts:
```bash
gh pr view PR_NUMBER --repo {self.github_repo} --json labels
```

**Step 4:** Check for conflicts:
- Parse labels for all "reviewer:*" labels
- If only YOUR label: SUCCESS - you claimed it!
- If multiple reviewer labels:
  - Alphabetically first slug wins
  - If you lost, remove your label and try another PR:
    ```bash
    gh issue edit PR_NUMBER --repo {self.github_repo} --remove-label "reviewer:{self.name_slug}"
    ```

### 3. If No PRs to Review

Check if project is complete:
```bash
gh issue list --repo {self.github_repo} --state open --json number
gh pr list --repo {self.github_repo} --state open --json number
```

If no open issues AND no open PRs, project is complete - exit with "exited:success".
Otherwise, wait {POLL_INTERVAL} seconds and check again.

### 4. Review the PR

a. Get PR details:
```bash
gh pr view PR_NUMBER --repo {self.github_repo} --json number,title,body,additions,deletions,changedFiles,headRefName
```

b. Get the diff (this is what you'll comment on):
```bash
gh pr diff PR_NUMBER --repo {self.github_repo}
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
- **Documentation**: Are changes documented if needed?

### 5b. Check for Merge Conflicts BEFORE Reviewing

Before doing a detailed review, check if the PR can even be merged:
```bash
gh pr view PR_NUMBER --repo {self.github_repo} --json mergeable,mergeStateStatus
```

**If `mergeable` is `false` or `mergeStateStatus` is `"DIRTY"` (has conflicts):**
- Do NOT proceed with a full review
- Request changes asking the author to resolve conflicts first:
  ```bash
  gh api repos/{owner}/{repo}/pulls/PR_NUMBER/reviews \\
    --method POST \\
    -f body="{format_signature(self.name, "Reviewer")} This PR has merge conflicts. Please rebase on main and resolve conflicts before I can review the code changes." \\
    -f event="REQUEST_CHANGES"
  ```
- Move on to the next PR
- Come back to this PR after the author fixes conflicts

**If `mergeable` is `true`:**
- Proceed with the code review below

### 6. Submit Review with Inline Comments

Use the GitHub API to submit a review with inline comments on specific lines.

**Collect your comments** as you review, noting:
- `path`: The file path (e.g., "src/utils.py")
- `line`: The line number in the NEW version of the file
- `body`: Your comment text (prefix with {format_inline_signature(self.name)})

**Submit a batch review with inline comments:**

For APPROVAL (code looks good):
```bash
gh api repos/{owner}/{repo}/pulls/PR_NUMBER/reviews \\
  --method POST \\
  -f body="{format_signature(self.name, "Reviewer")} Great work! Code looks good and is ready to merge." \\
  -f event="APPROVE" \\
  -f 'comments=[{{"path":"src/example.py","line":42,"body":"{format_inline_signature(self.name)} Nice implementation!"}}]'
```

For REQUESTING CHANGES (issues found):
```bash
gh api repos/{owner}/{repo}/pulls/PR_NUMBER/reviews \\
  --method POST \\
  -f body="{format_signature(self.name, "Reviewer")} Please address the inline comments before this can be merged." \\
  -f event="REQUEST_CHANGES" \\
  -f 'comments=[{{"path":"src/example.py","line":10,"body":"{format_inline_signature(self.name)} This could cause a null pointer exception"}},{{"path":"src/example.py","line":25,"body":"{format_inline_signature(self.name)} Consider using a constant here"}}]'
```

For COMMENT only (observations, no approval/rejection):
```bash
gh api repos/{owner}/{repo}/pulls/PR_NUMBER/reviews \\
  --method POST \\
  -f body="{format_signature(self.name, "Reviewer")} Some observations on the implementation." \\
  -f event="COMMENT" \\
  -f 'comments=[...]'
```

**Important notes on inline comments:**
- The `line` must be a line that appears in the diff (in the "+" side for additions)
- For multi-line comments, use `start_line` and `line` together
- Keep comments constructive and specific
- Always prefix inline comment bodies with {format_inline_signature(self.name)}

### 7. After Review - Programmer Merges After Approval

**If you APPROVED the PR:**
- You do NOT need to merge it yourself
- The programmer will see the approval and merge the PR themselves
- This avoids you being a bottleneck
- Remove your reviewer label since the review is complete:
  ```bash
  gh issue edit PR_NUMBER --repo {self.github_repo} --remove-label "reviewer:{self.name_slug}"
  ```
- Move on to review the next PR

**If you REQUESTED CHANGES:**
- Keep your reviewer label on the PR (so you get to re-review after fixes)
- The programmer will address feedback and push updates
- Next time you check, this PR will show up in your "PRs to re-review" list

### 8. Re-Reviewing After Changes

When a programmer pushes new commits after you requested changes:
1. Check the new commits/diff
2. Verify the issues were addressed
3. Submit a new review (APPROVE if fixed, REQUEST_CHANGES if not)
4. If approved, remove your reviewer label - the programmer will merge it themselves

### 9. When All Work is Done

If:
- No open PRs
- No open issues (all closed)

Then the project is complete. Update status to "exited:success" and exit.

## Important Notes

- Be thorough but efficient
- Use INLINE comments to give specific, actionable feedback
- Only merge PRs that YOU reviewed (your label is on them)
- Re-review PRs where you previously requested changes
- Keep your status file updated so the manager knows you're alive
- If you're unsure about something, ask the user for guidance
"""

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()
        return f"""Start reviewing PRs for the {self.project} project.

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
    name: str,
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
        name: Reviewer name (e.g., "Rachel Review").
        github_repo: GitHub repository in OWNER/REPO format.
        clone_path: Path to the main git clone.
        project: Project name.
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
        resume=resume,
        new_session=new_session,
        verbosity=verbosity,
        max_iterations=max_iterations,
    )
    await agent.run()
