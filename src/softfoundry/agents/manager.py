"""Manager agent that coordinates project setup and guides users to start other agents."""

import os
import sys
from pathlib import Path

from claude_agent_sdk import ResultMessage

from softfoundry.utils.github import LABEL_COLORS
from softfoundry.utils.loop import Agent, AgentConfig

AGENT_TYPE = "manager"
POLL_INTERVAL = 60  # seconds between monitoring cycles
DEFAULT_MAX_ITERATIONS = 100


class ManagerAgent(Agent):
    """Manager agent that coordinates project setup and monitors progress.

    This agent:
    1. Sets up the project (clone repo, create PROJECT.md, create issues)
    2. Guides the user to start programmer and reviewer agents
    3. Monitors project progress and releases stale tasks
    4. Determines when the project is complete (all issues closed)
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
        """Initialize the manager agent.

        Args:
            github_repo: GitHub repository in OWNER/REPO format.
            clone_path: Local path to clone the repo.
            project: Project name (derived from repo).
            resume: If True, automatically resume existing session.
            new_session: If True, force a new session.
            verbosity: Output verbosity level.
            max_iterations: Maximum loop iterations.
        """
        # Store agent-specific state
        self.github_repo = github_repo
        self.clone_path = clone_path
        self.project = project

        # Determine working directory
        cwd = str(Path(clone_path).resolve()) if Path(clone_path).exists() else None

        # Build config and delegate to parent
        config = AgentConfig(
            namespace=project,
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
        return f"""You are the Manager agent for the {self.project} project.

GitHub repo: {self.github_repo}
Local clone: {self.clone_path}
Status file: {self._status_path}

Your responsibilities:
1. Project setup and task planning
2. Guiding the user to start programmer/reviewer agents (they self-assign tasks)
3. Monitoring project progress and releasing stale tasks
4. Determining when the project is complete (all issues closed)

## Multi-Agent Context (IMPORTANT)

This project uses multiple AI agents (Manager, Programmers, Reviewers) that ALL share the SAME GitHub account. This means:

1. **All GitHub activity appears to come from the same user** - When you see issues, PRs, or comments, they may have been created by OTHER agents, not you.

2. **Do NOT be confused by "your own" activity** - If you see a PR, comment, or issue that you don't remember creating, it was likely created by another agent (a Programmer or Reviewer).

3. **Always identify yourself** - Since GitHub can't distinguish between agents, include your signature in all comments: **[Manager]:**

4. **Coordinate via labels, not usernames** - Use `assignee:{{slug}}` and `reviewer:{{slug}}` labels to track who is working on what, since GitHub's native assignment would show the same user for everyone.

5. **Trust the labels** - The labels are the source of truth for task assignment, not GitHub's author/assignee fields.

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

```bash
cat > {self._status_path} << 'EOF'
{{
  "agent_type": "manager",
  "project": "{self.project}",
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

3. Present the task plan for user verification:
   - Analyze PROJECT.md and derive all tasks that need to be created as GitHub issues
   - Present the plan as a numbered list with:
     - Task title
     - Brief description (1-2 sentences)
     - Proposed priority (high/medium/low)
   - Ask the user: "Are you happy with this plan, or do you have any suggestions?"
   - WAIT for user response before proceeding
   - If the user suggests changes, incorporate their feedback and present the revised plan
   - Only proceed to the next step once the user confirms they are satisfied

4. Create GitHub labels:
   ```bash
   gh label create "status:pending" --color "{LABEL_COLORS["status_pending"]}" --repo {self.github_repo} --force
   gh label create "status:in-progress" --color "{LABEL_COLORS["status_in_progress"]}" --repo {self.github_repo} --force
   gh label create "status:in-review" --color "{LABEL_COLORS["status_in_review"]}" --repo {self.github_repo} --force
   gh label create "priority:high" --color "{LABEL_COLORS["priority_high"]}" --repo {self.github_repo} --force
   gh label create "priority:medium" --color "{LABEL_COLORS["priority_medium"]}" --repo {self.github_repo} --force
   gh label create "priority:low" --color "{LABEL_COLORS["priority_low"]}" --repo {self.github_repo} --force
   ```

5. Create issues for each task based on the approved plan:
   ```bash
   gh issue create --repo {self.github_repo} --title "Task title" --body "Description" --label "status:pending,priority:medium"
   ```

NOTE: Do NOT assign tasks to programmers. Programmer agents will self-assign tasks by claiming them.

## Phase 2: Instruct User to Start Agents

After setup is complete, display clear instructions for starting agents.

Tell the user they can start AS MANY programmer and reviewer agents as they want.
Each programmer needs a unique name. Each reviewer needs a unique name.

**Example Programmer Commands (user can run multiple with different names):**
```bash
sf programmer --name "Alice Chen" \\
    --github-repo {self.github_repo} \\
    --clone-path {self.clone_path} \\
    --project {self.project}
```

```bash
sf programmer --name "Bob Smith" \\
    --github-repo {self.github_repo} \\
    --clone-path {self.clone_path} \\
    --project {self.project}
```

**Example Reviewer Commands (user can run multiple with different names):**
```bash
sf reviewer --name "Rachel Review" \\
    --github-repo {self.github_repo} \\
    --clone-path {self.clone_path} \\
    --project {self.project}
```

Explain that:
- Programmers will automatically find and claim unassigned tasks
- Reviewers will automatically find and claim PRs to review
- They can start as many agents as they want for parallelism
- Each agent needs a unique name for tracking

Then ask the user to type "ready" when they have started the agents.

## Phase 3: Monitor and Release Stale Tasks

Once agents are running, periodically:

### 1. Check for Stale Programmer Agents

Read all programmer status files and check for stale agents (no update in 5+ minutes):

```bash
# List all programmer status files
for f in ~/.softfoundry/agents/{self.project}/programmer-*.status; do
  if [ -f "$f" ]; then
    echo "=== $f ==="
    cat "$f"
    echo ""
  fi
done
```

For each status file, check the `last_update` timestamp. If more than 5 minutes old:
1. The agent is stale/dead
2. Check if it has a `current_issue` set
3. If yes, release that task and explain why:
   ```bash
   # Remove the assignee label
   gh issue edit ISSUE_NUMBER --repo {self.github_repo} --remove-label "assignee:SLUG"
   
   # Add explanatory comment
   gh issue comment ISSUE_NUMBER --repo {self.github_repo} --body "**[Manager]:** Released this task - the assigned programmer (AGENT_NAME) appears to be stale/unresponsive (no heartbeat for 5+ minutes). This task is now available for other programmers to claim."
   ```

### 2. Check for Stale Reviewer Agents

Similar process for reviewer status files:
```bash
for f in ~/.softfoundry/agents/{self.project}/reviewer-*.status; do
  if [ -f "$f" ]; then
    echo "=== $f ==="
    cat "$f"
    echo ""
  fi
done
```

If a reviewer is stale and has a `current_pr`, release that PR and explain why:
```bash
# Remove the reviewer label
gh issue edit PR_NUMBER --repo {self.github_repo} --remove-label "reviewer:SLUG"

# Add explanatory comment
gh pr comment PR_NUMBER --repo {self.github_repo} --body "**[Manager]:** Released this PR - the assigned reviewer (REVIEWER_NAME) appears to be stale/unresponsive (no heartbeat for 5+ minutes). Another reviewer may now claim this PR."
```

### 3. Check GitHub Progress

```bash
gh issue list --repo {self.github_repo} --state open --json number,title,labels
gh pr list --repo {self.github_repo} --state open --json number,title,state
```

### 4. Report Progress

Summarize:
- How many issues are open/closed
- How many PRs are open/merged
- Which agents are active/stale

### 5. Check for Completion

If all issues are closed and all PRs are merged, the project is complete!
- Update your status to "exited:success"
- Congratulate the user
- Say "PROJECT COMPLETE" clearly so the system knows to exit

## Communication

When you need user input (e.g., creating PROJECT.md), ask clear questions.
The user will respond, and you can continue from there.

Remember: Let Claude handle Git and GitHub operations directly using `gh` and `git` CLI.
"""

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()
        return f"""Start managing the {self.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}

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
    verbosity: str = "medium",
    resume: bool = False,
    new_session: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> None:
    """Run the manager agent.

    Args:
        github_repo: GitHub repository in OWNER/REPO format (prompted if None).
        clone_path: Local path to clone the repo (defaults to castings/{project}).
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

    # Derive project name from repo
    project = github_repo.split("/")[-1]

    # Default clone path
    if not clone_path:
        clone_path = f"castings/{project}"

    agent = ManagerAgent(
        github_repo=github_repo,
        clone_path=clone_path,
        project=project,
        resume=resume,
        new_session=new_session,
        verbosity=verbosity,
        max_iterations=max_iterations,
    )
    await agent.run()
