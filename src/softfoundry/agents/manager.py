"""Manager agent that coordinates project setup and guides users to start other agents."""

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
from softfoundry.utils.github import LABEL_COLORS

AGENT_TYPE = "manager"
POLL_INTERVAL = 60  # seconds between monitoring cycles
DEFAULT_MAX_ITERATIONS = 100


class ManagerAgent(Agent):
    """Manager agent that coordinates project setup and monitors progress.

    This agent:
    1. Sets up the project (clone repo, create PROJECT.md, find/create epic)
    2. Creates sub-issues linked to the epic for programmers to work on
    3. Guides the user to start programmer and reviewer agents
    4. Monitors project progress and releases stale tasks
    5. Determines when the project is complete (all sub-issues closed)
    """

    def __init__(
        self,
        github_repo: str,
        clone_path: str,
        project: str,
        epic: int | None = None,
        resume: bool = False,
        new_session: bool = False,
        verbosity: str = "medium",
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        dry_mode: bool = False,
    ):
        """Initialize the manager agent.

        Args:
            github_repo: GitHub repository in OWNER/REPO format.
            clone_path: Local path to clone the repo.
            project: Project name (derived from repo).
            epic: GitHub issue number to use as the top-level epic (optional).
            resume: If True, automatically resume existing session.
            new_session: If True, force a new session.
            verbosity: Output verbosity level.
            max_iterations: Maximum loop iterations.
            dry_mode: If True, skip session resolution and status file writes.
        """
        # Store agent-specific state
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
        config = AgentConfig(
            namespace=project,
            agent_type=AGENT_TYPE,
            agent_name="manager",
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
                "mcp__orchestrator__create_sub_issue",
                "mcp__orchestrator__close_epic",
                "mcp__orchestrator__create_issue",
                "mcp__orchestrator__list_issues",
                "mcp__orchestrator__list_my_sub_issues",
                # PR tools
                "mcp__orchestrator__list_open_prs",
                "mcp__orchestrator__list_my_reviews",
                # Comment tools
                "mcp__orchestrator__comment_on_issue",
                "mcp__orchestrator__comment_on_pr",
                # Label tools
                "mcp__orchestrator__list_labels",
                "mcp__orchestrator__create_label",
                "mcp__orchestrator__update_issue_labels",
                # Activity tools
                "mcp__orchestrator__log_activity",
                "mcp__orchestrator__get_activity_log",
                # Agent health tools
                "mcp__orchestrator__list_stale_agents",
                # Unassignment tools
                "mcp__orchestrator__unassign_programmer",
                "mcp__orchestrator__unassign_reviewer",
            ],
            mcp_servers={
                "orchestrator": orchestrator,
            },
            permission_mode="acceptEdits",
            cwd=cwd,
            max_iterations=max_iterations,
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
        """Generate the system prompt for the manager agent."""
        project_info = project_info_prompt(
            github_repo=self.github_repo,
            epic=self.epic,
            clone_path=self.clone_path,
            status_path=self._status_path,
        )

        return f"""You are the Manager agent for the {self.project} project.

{project_info}

## Responsibilities

1. Setup: clone repo, find/create PROJECT.md, find/create the epic, create labels
2. Plan tasks: create sub-issues under the epic with dependencies
3. Instruct user to start programmer/reviewer agents (they self-assign)
4. Monitor progress, release stale tasks, close epic when done

{orchestrator_mcp_tools_prompt()}

## Status File Updates

CRITICAL: You MUST update your status file frequently using Bash:

```bash
cat > {self._status_path} << 'EOF'
{{
  "agent_type": "manager",
  "project": "{self.project}",
  "status": "working",
  "details": "Description of what you're doing",
  "current_epic": EPIC_NUMBER,
  "last_update": "$(date -Iseconds)",
  "pid": {os.getpid()}
}}
EOF
```

## Phase 1: Setup

**1.1 Clone** (if needed): `git clone https://github.com/{self.github_repo} {self.clone_path}`

**1.2 PROJECT.md**: If missing, use `mcp__user__ask_user` to ask the user about \
scope/tech/features, write it, commit and push.

**1.3 Create Labels**: First use `mcp__orchestrator__list_labels` to check which labels \
already exist in the repository. Then only create missing labels using \
`mcp__orchestrator__create_label`. Required labels:
- `type:epic` (color {LABEL_COLORS["type_epic"]})
- `status:pending` (color {LABEL_COLORS["status_pending"]})
- `status:in-progress` (color {LABEL_COLORS["status_in_progress"]})
- `status:in-review` (color {LABEL_COLORS["status_in_review"]})
- `status:feedback-requested` (color {LABEL_COLORS["status_feedback_requested"]})
- `status:approved` (color {LABEL_COLORS["status_approved"]})
- `priority:high` (color {LABEL_COLORS["priority_high"]})
- `priority:medium` (color {LABEL_COLORS["priority_medium"]})
- `priority:low` (color {LABEL_COLORS["priority_low"]}).

**1.4 Find or Create Epic**:
- If an epic was provided: verify it exists with `mcp__orchestrator__get_epic_status`, \
  add `type:epic` label if needed, read its goals.
- If no epic was provided, use `mcp__user__ask_user` to start a discussion with the \
  user to understand what they want to work on. Keep an interactive discussion with \
  the user (using `mcp__user__ask_user` for each question) until the user is satisfied, \
  then create the epic accordingly.

## Phase 2: Plan Tasks

**2.1 Plan Sub-Tasks**: Read PROJECT.md + epic to derive tasks. Present a numbered plan
with title, description, priority, and dependencies.

Dependency guidelines:
- Identify independent foundational tasks (no dependencies)
- List only direct dependencies per task (not transitive)
- Tasks without dependencies can be worked in parallel

Use `mcp__user__ask_user` to present the plan and ask the user to review and confirm \
it.  If the user is not happy with the plan, keep an interactive discussion (using \
`mcp__user__ask_user` for each question) until the user is satisfied. When the user \
confirms the plan, proceed with creating the sub-issues.

**2.2 Create Sub-Issues**:

- Create in dependency order (independent first) using `mcp__orchestrator__create_sub_issue`.
- For dependent tasks, pass the `depends_on` to `mcp__orchestrator__create_sub_issue` as
  comma-separated issue numbers. This ensures programmers can't claim blocked tasks.
- Log activity after creating all sub-issues.

## Phase 3: Instruct User to Start Agents

Show commands for starting agents. Users can run as many as they want with unique names.
Replace EPIC_NUMBER with the actual number.

```bash
# Programmer
uv run sf programmer \\
    --name "<Programmer Name>" \\
    --github-repo {self.github_repo} \\
    --clone-path {self.clone_path} \\
    --project {self.project} \\
    --epic EPIC_NUMBER

# Reviewer
uv run sf reviewer \\
    --name "<Reviewer Name>" \\
    --github-repo {self.github_repo} \\
    --clone-path {self.clone_path} \\
    --project {self.project} \\
    --epic EPIC_NUMBER
```

Explain to the user that:

- Programmer agents auto-claim sub-tasks
- Reviewer agents auto-claim PRs
- Each agent needs a unique name
- Each agent needs to be started in a separate terminal

Use `mcp__user__ask_user` to ask the user to confirm when agents are started.

## Phase 4: Monitor

Periodically:

1. **Epic progress**: Use `get_epic_status` to check the status of the epic. 

2. **Stale agents**: Use `mcp__orchestrator__list_stale_agents` with project "{self.project}" to detect
non-responsive agents. For each stale agent found:
   - If `agent_type` is "programmer": use `mcp__orchestrator__list_my_sub_issues` with
     the agent's name to find their assigned issues on GitHub, then call
     `mcp__orchestrator__unassign_programmer` for each (provide a comment explaining the
     agent was non-responsive).
   - If `agent_type` is "reviewer": use `mcp__orchestrator__list_my_reviews` with the
     agent's name to find their assigned PRs on GitHub, then call
     `mcp__orchestrator__unassign_reviewer` for each (provide a comment explaining the
     agent was non-responsive).

3. **PR status**: `mcp__orchestrator__list_open_prs` to track open PRs.

4. **Report**: Summarize sub-issues (open/closed), PRs (open/merged).

5. **Completion**: When `completed_sub_issues == total_sub_issues` and all PRs merged:
close epic with `mcp__orchestrator__close_epic`, log completion, update status to
"exited:success", and inform the user that the project is complete.
"""

    def get_initial_prompt(self) -> str:
        """Build the first prompt, including crash-recovery context."""
        resume_context = self._get_resume_context()

        if self.epic:
            epic_instruction = f"""
An epic issue was provided: #{self.epic}
Verify this issue exists and use it as the parent for all sub-tasks.
"""
        else:
            epic_instruction = """
No epic was provided. You will need to
1. Use the mcp__orchestrator__list_issues tool to check if there's already an active \
   epic (open issue with `type:epic` label).
2. If yes, use `mcp__user__ask_user_choice` to ask the user if they want to work on \
   that epic or create a new one.
3. If no, use `mcp__user__ask_user` to start a discussion with the user to understand \
   what they want to work on. Keep an interactive discussion (using \
   `mcp__user__ask_user` for each question) until the user is satisfied, then create \
   the epic accordingly.
"""

        return f"""Start managing the {self.project} project.

GitHub repo: {self.github_repo}
Clone path: {self.clone_path}
{epic_instruction}
{resume_context}
"""

    def _get_resume_context(self) -> str:
        """Check status file for crash recovery context."""
        existing_status = self.read_status()
        if not existing_status:
            return ""

        status = existing_status.get("status", "")
        if status.startswith("exited:"):
            return ""  # Clean exit, no recovery needed

        context_parts = []
        if existing_status.get("details"):
            context_parts.append(f"""IMPORTANT: You previously crashed or were interrupted.
Your last status was: {status}
You were doing: {existing_status.get("details")}""")

        # TODO Do we need this? It is already in the system and initial prompts.
        if existing_status.get("current_epic"):
            context_parts.append(
                f"You were working on epic #{existing_status.get('current_epic')}."
            )

        if context_parts:
            context_parts.append(
                "Check the current state and continue from where you left off."
            )
            return "\n".join(context_parts)

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
        if self._app:
            self._app.add_lifecycle_message(
                "Project completed successfully!", "success"
            )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


async def run_manager(
    github_repo: str | None,
    clone_path: str | None,
    epic: int | None = None,
    verbosity: str = "medium",
    resume: bool = False,
    new_session: bool = False,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    print_prompts_and_exit: bool = False,
) -> None:
    """Run the manager agent.

    Args:
        github_repo: GitHub repository in OWNER/REPO format (prompted if None).
        clone_path: Local path to clone the repo (defaults to castings/{project}).
        epic: GitHub issue number to use as the top-level epic (optional).
        verbosity: Output verbosity level (minimal, medium, verbose).
        resume: If True, automatically resume existing session.
        new_session: If True, always start a new session.
        max_iterations: Maximum loop iterations (safety limit).
        print_prompts_and_exit: If True, print prompts and exit without running.
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

    if print_prompts_and_exit:
        agent = ManagerAgent(
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

    agent = ManagerAgent(
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
