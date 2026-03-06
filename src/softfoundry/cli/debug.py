"""Debug command for directly invoking orchestrator MCP tools without an LLM.

Usage:
    sf debug get-epic-status --epic-number 42
    sf debug get-epic-status --epic-number 42 --github-repo owner/repo
    sf debug claim-sub-issue --epic-number 42 --sub-issue-number 5 --agent-name "Alice Chen"
"""

import asyncio
import json
import sys
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel

from softfoundry.mcp import constants, orchestrator
from softfoundry.mcp.github_client import GitHubClient

console = Console()
error_console = Console(stderr=True)

debug_app = typer.Typer(
    help="Debug orchestrator MCP tools by invoking them directly.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Shared state and helpers
# ---------------------------------------------------------------------------

# Global option for raw JSON output (no Rich formatting)
_raw_output: bool = False


@debug_app.callback()
def debug_callback(
    github_repo: Annotated[
        str,
        typer.Option(
            help="GitHub repository in OWNER/REPO format.",
        ),
    ] = constants.DEFAULT_GITHUB_REPO,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Output raw JSON without Rich formatting."),
    ] = False,
) -> None:
    """Initialize the GitHub client for all debug subcommands."""
    global _raw_output
    _raw_output = raw

    parts = github_repo.split("/")
    if len(parts) != 2:
        error_console.print(
            f"[red]Invalid --github-repo format: {github_repo}. Expected OWNER/REPO[/red]"
        )
        raise typer.Exit(code=1)

    owner, repo = parts
    orchestrator._github_client = GitHubClient(owner, repo)


def _run_tool(coro: Any) -> None:
    """Run an async orchestrator tool and display the result."""
    result: dict[str, Any] = asyncio.run(coro)

    is_error = result.get("isError", False)
    content = result.get("content", [])
    text = content[0]["text"] if content else ""

    if is_error:
        if _raw_output:
            print(text, file=sys.stderr)
        else:
            error_console.print(Panel(text, title="Error", border_style="red"))
        raise typer.Exit(code=1)

    if _raw_output:
        print(text)
    else:
        # Try to parse as JSON for pretty display; fall back to plain text
        try:
            parsed = json.loads(text)
            console.print(JSON(json.dumps(parsed, indent=2, default=str)))
        except (json.JSONDecodeError, TypeError):
            console.print(text)


# =============================================================================
# Epic / Issue Tools
# =============================================================================


@debug_app.command(
    "get-epic-status", help="Get the status of an epic with all its sub-issues."
)
def get_epic_status(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
) -> None:
    _run_tool(orchestrator.impl_get_epic_status({"epic_number": epic_number}))


@debug_app.command("get-sub-issue", help="Get detailed status of a specific sub-issue.")
def get_sub_issue(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
    sub_issue_number: Annotated[int, typer.Option(help="Sub-issue number.")],
) -> None:
    _run_tool(
        orchestrator.impl_get_sub_issue(
            {"epic_number": epic_number, "sub_issue_number": sub_issue_number}
        )
    )


@debug_app.command(
    "list-available-sub-issues",
    help="List unassigned pending sub-issues (filters out unresolved dependencies).",
)
def list_available_sub_issues(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
    priority: Annotated[
        str | None,
        typer.Option(help="Filter by priority: high, medium, low."),
    ] = None,
) -> None:
    args: dict[str, Any] = {"epic_number": epic_number}
    if priority is not None:
        args["priority"] = priority
    _run_tool(orchestrator.impl_list_available_sub_issues(args))


@debug_app.command(
    "list-my-sub-issues", help="List sub-issues assigned to a specific agent."
)
def list_my_sub_issues(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
    agent_name: Annotated[str, typer.Option(help='Agent name, e.g. "Alice Chen".')],
) -> None:
    _run_tool(
        orchestrator.impl_list_my_sub_issues(
            {"epic_number": epic_number, "agent_name": agent_name}
        )
    )


@debug_app.command(
    "claim-sub-issue", help="Claim an unassigned sub-issue by adding assignee label."
)
def claim_sub_issue(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
    sub_issue_number: Annotated[int, typer.Option(help="Sub-issue number to claim.")],
    agent_name: Annotated[str, typer.Option(help='Agent name, e.g. "Alice Chen".')],
) -> None:
    _run_tool(
        orchestrator.impl_claim_sub_issue(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_issue_number,
                "agent_name": agent_name,
            }
        )
    )


@debug_app.command(
    "update-sub-issue-status", help="Update the status label on a sub-issue."
)
def update_sub_issue_status(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
    sub_issue_number: Annotated[int, typer.Option(help="Sub-issue number.")],
    new_status: Annotated[
        str,
        typer.Option(help="New status: pending, in-progress, or in-review."),
    ],
) -> None:
    _run_tool(
        orchestrator.impl_update_sub_issue_status(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_issue_number,
                "new_status": new_status,
            }
        )
    )


@debug_app.command(
    "create-sub-issue", help="Create a new sub-issue and link it to the epic."
)
def create_sub_issue(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
    title: Annotated[str, typer.Option(help="Sub-issue title.")],
    body: Annotated[str, typer.Option(help="Sub-issue body/description.")],
    priority: Annotated[str, typer.Option(help="Priority: high, medium, or low.")],
    agent_name: Annotated[
        str, typer.Option(help='Author agent name, e.g. "Alice Chen".')
    ] = "",
    agent_type: Annotated[
        str, typer.Option(help="Author agent type: manager, programmer, reviewer.")
    ] = "",
    depends_on: Annotated[
        str,
        typer.Option(
            help='Comma-separated issue numbers this depends on, e.g. "3,5". Empty for none.'
        ),
    ] = "",
) -> None:
    _run_tool(
        orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": title,
                "body": body,
                "priority": priority,
                "depends_on": depends_on,
                "agent_name": agent_name,
                "agent_type": agent_type,
            }
        )
    )


@debug_app.command("close-epic", help="Close the epic issue.")
def close_epic(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
) -> None:
    _run_tool(orchestrator.impl_close_epic({"epic_number": epic_number}))


# =============================================================================
# PR Tools
# =============================================================================


@debug_app.command(
    "get-pr-status", help="Get the status of a pull request including feedback flag."
)
def get_pr_status(
    pr_number: Annotated[int, typer.Option(help="Pull request number.")],
) -> None:
    _run_tool(orchestrator.impl_get_pr_status({"pr_number": pr_number}))


@debug_app.command(
    "list-my-prs", help="List pull requests created by a specific author."
)
def list_my_prs(
    author_name: Annotated[str, typer.Option(help='Author name, e.g. "Alice Chen".')],
) -> None:
    _run_tool(orchestrator.impl_list_my_prs({"author_name": author_name}))


@debug_app.command(
    "list-my-reviews", help="List pull requests assigned to a specific reviewer."
)
def list_my_reviews(
    reviewer_name: Annotated[
        str, typer.Option(help='Reviewer name, e.g. "Rachel Review".')
    ],
) -> None:
    _run_tool(orchestrator.impl_list_my_reviews({"reviewer_name": reviewer_name}))


@debug_app.command(
    "list-prs-for-review",
    help="List pull requests awaiting review (not assigned to a reviewer).",
)
def list_prs_for_review(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
) -> None:
    _run_tool(orchestrator.impl_list_prs_for_review({"epic_number": epic_number}))


@debug_app.command(
    "claim-pr-review", help="Claim a PR for review by adding reviewer label."
)
def claim_pr_review(
    pr_number: Annotated[int, typer.Option(help="Pull request number.")],
    reviewer_name: Annotated[
        str, typer.Option(help='Reviewer name, e.g. "Rachel Review".')
    ],
) -> None:
    _run_tool(
        orchestrator.impl_claim_pr_review(
            {"pr_number": pr_number, "reviewer_name": reviewer_name}
        )
    )


@debug_app.command(
    "request-changes",
    help="Request changes on a PR. Posts review comments and adds feedback-requested label.",
)
def request_changes(
    pr_number: Annotated[int, typer.Option(help="Pull request number.")],
    agent_name: Annotated[str, typer.Option(help='Agent name, e.g. "Rachel Review".')],
    agent_type: Annotated[str, typer.Option(help="Agent type: reviewer.")] = "reviewer",
    comment: Annotated[
        str, typer.Option(help="Review comment describing changes needed.")
    ] = "",
    inline_comments: Annotated[
        str,
        typer.Option(
            help='Newline-separated inline comments in "path:line:body" format.'
        ),
    ] = "",
) -> None:
    _run_tool(
        orchestrator.impl_request_changes(
            {
                "pr_number": pr_number,
                "agent_name": agent_name,
                "agent_type": agent_type,
                "comment": comment,
                "inline_comments": inline_comments,
            }
        )
    )


@debug_app.command(
    "mark-feedback-addressed",
    help="Mark that feedback has been addressed (removes feedback-requested label).",
)
def mark_feedback_addressed(
    pr_number: Annotated[int, typer.Option(help="Pull request number.")],
    agent_name: Annotated[str, typer.Option(help='Agent name, e.g. "Bob Smith".')],
    agent_type: Annotated[
        str, typer.Option(help="Agent type: programmer.")
    ] = "programmer",
    comment: Annotated[
        str, typer.Option(help="Comment to post.")
    ] = "Feedback has been addressed. Ready for re-review.",
) -> None:
    _run_tool(
        orchestrator.impl_mark_feedback_addressed(
            {
                "pr_number": pr_number,
                "agent_name": agent_name,
                "agent_type": agent_type,
                "comment": comment,
            }
        )
    )


@debug_app.command("approve-pr", help="Approve a pull request.")
def approve_pr(
    pr_number: Annotated[int, typer.Option(help="Pull request number.")],
    agent_name: Annotated[str, typer.Option(help='Agent name, e.g. "Rachel Review".')],
    agent_type: Annotated[str, typer.Option(help="Agent type: reviewer.")] = "reviewer",
    comment: Annotated[str, typer.Option(help="Approval comment.")] = "LGTM!",
) -> None:
    _run_tool(
        orchestrator.impl_approve_pr(
            {
                "pr_number": pr_number,
                "agent_name": agent_name,
                "agent_type": agent_type,
                "comment": comment,
            }
        )
    )


@debug_app.command(
    "get-pr-feedback",
    help="Get combined reviews and inline diff-level comments for a PR.",
)
def get_pr_feedback(
    pr_number: Annotated[int, typer.Option(help="Pull request number.")],
) -> None:
    _run_tool(orchestrator.impl_get_pr_feedback({"pr_number": pr_number}))


@debug_app.command("get-pr-diff", help="Get the diff text of a pull request.")
def get_pr_diff(
    pr_number: Annotated[int, typer.Option(help="Pull request number.")],
) -> None:
    _run_tool(orchestrator.impl_get_pr_diff({"pr_number": pr_number}))


@debug_app.command("create-pr", help="Create a pull request with agent signature.")
def create_pr(
    title: Annotated[str, typer.Option(help="PR title.")],
    body: Annotated[str, typer.Option(help="PR body/description.")],
    head_branch: Annotated[str, typer.Option(help="Branch containing changes.")],
    base_branch: Annotated[str, typer.Option(help="Branch to merge into.")] = "main",
    agent_name: Annotated[
        str, typer.Option(help='Author agent name, e.g. "Alice Chen".')
    ] = "",
    agent_type: Annotated[
        str, typer.Option(help="Agent type: programmer.")
    ] = "programmer",
    labels: Annotated[
        str,
        typer.Option(help='Comma-separated labels, e.g. "assignee:alice-chen".'),
    ] = "",
) -> None:
    _run_tool(
        orchestrator.impl_create_pr(
            {
                "title": title,
                "body": body,
                "head_branch": head_branch,
                "base_branch": base_branch,
                "agent_name": agent_name,
                "agent_type": agent_type,
                "labels": labels,
            }
        )
    )


@debug_app.command("merge-pr", help="Merge a pull request.")
def merge_pr(
    pr_number: Annotated[int, typer.Option(help="Pull request number.")],
    method: Annotated[
        str, typer.Option(help="Merge method: merge, squash, rebase.")
    ] = "squash",
    delete_branch: Annotated[
        bool, typer.Option(help="Delete head branch after merge.")
    ] = True,
) -> None:
    _run_tool(
        orchestrator.impl_merge_pr(
            {
                "pr_number": pr_number,
                "method": method,
                "delete_branch": delete_branch,
            }
        )
    )


# =============================================================================
# Comment Tools
# =============================================================================


@debug_app.command(
    "comment-on-issue", help="Post a comment on an issue with agent signature."
)
def comment_on_issue(
    issue_number: Annotated[int, typer.Option(help="Issue number.")],
    agent_name: Annotated[str, typer.Option(help='Agent name, e.g. "Alice Chen".')],
    agent_type: Annotated[str, typer.Option(help="Agent type.")],
    comment: Annotated[str, typer.Option(help="Comment text.")],
) -> None:
    _run_tool(
        orchestrator.impl_comment_on_issue(
            {
                "issue_number": issue_number,
                "agent_name": agent_name,
                "agent_type": agent_type,
                "comment": comment,
            }
        )
    )


@debug_app.command("comment-on-pr", help="Post a comment on a PR with agent signature.")
def comment_on_pr(
    pr_number: Annotated[int, typer.Option(help="Pull request number.")],
    agent_name: Annotated[str, typer.Option(help='Agent name, e.g. "Alice Chen".')],
    agent_type: Annotated[str, typer.Option(help="Agent type.")],
    comment: Annotated[str, typer.Option(help="Comment text.")],
) -> None:
    _run_tool(
        orchestrator.impl_comment_on_pr(
            {
                "pr_number": pr_number,
                "agent_name": agent_name,
                "agent_type": agent_type,
                "comment": comment,
            }
        )
    )


# =============================================================================
# Label Tools
# =============================================================================


@debug_app.command("create-label", help="Create or update a GitHub label.")
def create_label(
    name: Annotated[str, typer.Option(help="Label name.")],
    color: Annotated[
        str, typer.Option(help='Label color (hex without #, e.g. "d73a4a").')
    ],
    description: Annotated[str, typer.Option(help="Label description.")] = "",
) -> None:
    _run_tool(
        orchestrator.impl_create_label(
            {"name": name, "color": color, "description": description}
        )
    )


@debug_app.command(
    "update-issue-labels", help="Add or remove labels on an issue or PR."
)
def update_issue_labels(
    issue_number: Annotated[int, typer.Option(help="Issue or PR number.")],
    add_labels: Annotated[
        str, typer.Option(help="Comma-separated labels to add.")
    ] = "",
    remove_labels: Annotated[
        str, typer.Option(help="Comma-separated labels to remove.")
    ] = "",
) -> None:
    _run_tool(
        orchestrator.impl_update_issue_labels(
            {
                "issue_number": issue_number,
                "add_labels": add_labels,
                "remove_labels": remove_labels,
            }
        )
    )


# =============================================================================
# Issue Tools
# =============================================================================


@debug_app.command("create-issue", help="Create a standalone issue.")
def create_issue(
    title: Annotated[str, typer.Option(help="Issue title.")],
    body: Annotated[str, typer.Option(help="Issue body/description.")],
    labels: Annotated[
        str, typer.Option(help='Comma-separated labels, e.g. "type:epic".')
    ] = "",
) -> None:
    _run_tool(
        orchestrator.impl_create_issue({"title": title, "body": body, "labels": labels})
    )


@debug_app.command("list-issues", help="List issues filtered by labels and state.")
def list_issues(
    labels: Annotated[
        str, typer.Option(help='Comma-separated label filter, e.g. "type:epic".')
    ] = "",
    state: Annotated[
        str, typer.Option(help="State filter: open, closed, all.")
    ] = "open",
) -> None:
    _run_tool(orchestrator.impl_list_issues({"labels": labels, "state": state}))


@debug_app.command("list-open-prs", help="List all open pull requests.")
def list_open_prs() -> None:
    _run_tool(orchestrator.impl_list_open_prs({}))


# =============================================================================
# Activity Tools
# =============================================================================


@debug_app.command(
    "log-activity", help="Post an activity log comment on the epic issue."
)
def log_activity(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
    agent_name: Annotated[str, typer.Option(help='Agent name, e.g. "Alice Chen".')],
    agent_type: Annotated[
        str, typer.Option(help="Agent type: manager, programmer, or reviewer.")
    ],
    event_type: Annotated[
        str,
        typer.Option(
            help="Event type: started, claimed, progress, pr_created, review_started, "
            "review_submitted, feedback_addressed, merged, completed, idle, error."
        ),
    ],
    message: Annotated[str, typer.Option(help="Activity message.")],
    issue_number: Annotated[
        int | None, typer.Option(help="Related issue number (optional).")
    ] = None,
    pr_number: Annotated[
        int | None, typer.Option(help="Related PR number (optional).")
    ] = None,
) -> None:
    args: dict[str, Any] = {
        "epic_number": epic_number,
        "agent_name": agent_name,
        "agent_type": agent_type,
        "event_type": event_type,
        "message": message,
    }
    if issue_number is not None:
        args["issue_number"] = issue_number
    if pr_number is not None:
        args["pr_number"] = pr_number
    _run_tool(orchestrator.impl_log_activity(args))


@debug_app.command(
    "get-activity-log", help="Get recent activity log entries from the epic."
)
def get_activity_log(
    epic_number: Annotated[int, typer.Option(help="Epic issue number.")],
    limit: Annotated[
        int, typer.Option(help="Maximum number of entries to return.")
    ] = 20,
) -> None:
    _run_tool(
        orchestrator.impl_get_activity_log({"epic_number": epic_number, "limit": limit})
    )


# =============================================================================
# Registration
# =============================================================================


def register_command(app: typer.Typer) -> tuple:
    """Register the debug command group with the Typer app."""
    app.add_typer(debug_app, name="debug")
    return (debug_app,)
