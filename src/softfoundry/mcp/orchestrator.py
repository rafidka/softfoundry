"""MCP orchestration server for softfoundry.

This module provides an MCP server that all agents use for GitHub coordination.
It offers structured access to epic/sub-issue state, PR status, and activity logging.

The module is organized as:
- Helper functions (_success, _error, etc.)
- Implementation functions (impl_*) - testable business logic
- Tool-decorated wrappers (tool_*) - MCP integration
- Server factory (create_orchestrator_server)
"""

from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from pydantic import BaseModel

from softfoundry.mcp.github_client import GitHubClient, GitHubClientError


# Module-level client instance (set by create_orchestrator_server or tests)
_github_client: GitHubClient | None = None


def _get_client() -> GitHubClient:
    """Get the GitHub client instance."""
    if _github_client is None:
        raise RuntimeError(
            "GitHub client not initialized. Call create_orchestrator_server() first."
        )
    return _github_client


def _success(data: Any) -> dict[str, Any]:
    """Create a success response."""
    return {"content": [{"type": "text", "text": str(data)}]}


def _json_success(data: Any) -> dict[str, Any]:
    """Create a success response with JSON data."""
    import json

    if isinstance(data, BaseModel):
        data = data.model_dump()
    elif isinstance(data, list) and data and isinstance(data[0], BaseModel):
        data = [item.model_dump() for item in data]
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}]
    }


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}


# =============================================================================
# Implementation Functions (testable)
# =============================================================================


async def impl_get_epic_status(args: dict[str, Any]) -> dict[str, Any]:
    """Get the status of an epic with all its sub-issues."""
    try:
        client = _get_client()
        epic_status = await client.get_epic_status(args["epic_number"])
        return _json_success(epic_status)
    except GitHubClientError as e:
        return _error(str(e))


async def impl_get_sub_issue(args: dict[str, Any]) -> dict[str, Any]:
    """Get detailed status of a specific sub-issue."""
    try:
        client = _get_client()
        sub_issue = await client.get_sub_issue_status(
            args["epic_number"], args["sub_issue_number"]
        )
        return _json_success(sub_issue)
    except GitHubClientError as e:
        return _error(str(e))


async def impl_list_available_sub_issues(args: dict[str, Any]) -> dict[str, Any]:
    """List unassigned pending sub-issues from an epic.

    Automatically filters out tasks whose dependencies are not yet resolved
    (i.e., all issues in depends_on must be closed before a task is available).
    """
    try:
        client = _get_client()
        epic_status = await client.get_epic_status(args["epic_number"])

        # Build set of closed sub-issue numbers for dependency resolution
        closed_issues = {
            si.number for si in epic_status.sub_issues if si.state == "closed"
        }

        # Filter to unassigned pending issues
        available = [
            si
            for si in epic_status.sub_issues
            if si.state == "open"
            and si.assignee is None
            and si.status in (None, "pending")
        ]

        # Filter out tasks with unresolved dependencies
        available = [
            si for si in available if all(dep in closed_issues for dep in si.depends_on)
        ]

        # Filter by priority if specified
        priority = args.get("priority")
        if priority:
            available = [si for si in available if si.priority == priority]

        return _json_success(available)
    except GitHubClientError as e:
        return _error(str(e))


async def impl_list_my_sub_issues(args: dict[str, Any]) -> dict[str, Any]:
    """List sub-issues assigned to a specific agent."""
    try:
        client = _get_client()
        epic_status = await client.get_epic_status(args["epic_number"])

        # Convert agent name to slug format (e.g., "Alice Chen" -> "alice-chen")
        agent_slug = args["agent_name"].lower().replace(" ", "-")

        # Filter to assigned issues
        my_issues = [si for si in epic_status.sub_issues if si.assignee == agent_slug]

        return _json_success(my_issues)
    except GitHubClientError as e:
        return _error(str(e))


async def impl_claim_sub_issue(args: dict[str, Any]) -> dict[str, Any]:
    """Claim an unassigned sub-issue by adding assignee label.

    Validates that all dependencies are resolved (closed) before allowing
    the claim. This prevents agents from working on tasks whose
    prerequisites are not yet complete.
    """
    try:
        client = _get_client()

        # Get epic status to check dependencies
        epic_status = await client.get_epic_status(args["epic_number"])

        # Find the sub-issue in the epic
        sub_issue = None
        for si in epic_status.sub_issues:
            if si.number == args["sub_issue_number"]:
                sub_issue = si
                break

        if sub_issue is None:
            return _error(
                f"Sub-issue #{args['sub_issue_number']} is not part of epic #{args['epic_number']}"
            )

        if sub_issue.assignee is not None:
            return _error(
                f"Sub-issue #{args['sub_issue_number']} is already assigned to {sub_issue.assignee}"
            )

        # Check dependencies — all must be closed
        if sub_issue.depends_on:
            closed_issues = {
                si.number for si in epic_status.sub_issues if si.state == "closed"
            }
            unresolved = [
                dep for dep in sub_issue.depends_on if dep not in closed_issues
            ]
            if unresolved:
                dep_str = ", ".join(f"#{d}" for d in unresolved)
                return _error(
                    f"Sub-issue #{args['sub_issue_number']} is blocked by unresolved dependencies: {dep_str}. "
                    f"These tasks must be completed before this one can be claimed."
                )

        # Convert agent name to slug
        agent_slug = args["agent_name"].lower().replace(" ", "-")

        # Add assignee label and update status
        await client.update_issue_labels(
            args["sub_issue_number"],
            add_labels=[f"assignee:{agent_slug}", "status:in-progress"],
            remove_labels=["status:pending"],
        )

        return _success(
            f"Successfully claimed sub-issue #{args['sub_issue_number']} for {args['agent_name']}"
        )
    except GitHubClientError as e:
        return _error(str(e))


async def impl_update_sub_issue_status(args: dict[str, Any]) -> dict[str, Any]:
    """Update the status label on a sub-issue.

    Valid statuses: pending, in-progress, in-review
    """
    try:
        client = _get_client()
        new_status = args["new_status"]

        if new_status not in ("pending", "in-progress", "in-review"):
            return _error(
                f"Invalid status: {new_status}. Must be pending, in-progress, or in-review"
            )

        # First verify the sub-issue is part of the epic
        await client.get_sub_issue_status(args["epic_number"], args["sub_issue_number"])

        # Remove all status labels and add the new one
        await client.update_issue_labels(
            args["sub_issue_number"],
            add_labels=[f"status:{new_status}"],
            remove_labels=["status:pending", "status:in-progress", "status:in-review"],
        )

        return _success(
            f"Updated sub-issue #{args['sub_issue_number']} status to {new_status}"
        )
    except GitHubClientError as e:
        return _error(str(e))


async def impl_create_sub_issue(args: dict[str, Any]) -> dict[str, Any]:
    """Create a new sub-issue and link it to the epic."""
    try:
        client = _get_client()

        priority = args.get("priority", "medium")
        if priority not in ("high", "medium", "low"):
            priority = "medium"

        # Parse depends_on from comma-separated string (e.g. "3,5,7" or "")
        depends_on_str = args.get("depends_on", "")
        depends_on: list[int] = []
        if depends_on_str:
            depends_on = [
                int(d.strip().lstrip("#"))
                for d in depends_on_str.split(",")
                if d.strip()
            ]

        # Build the issue body, appending dependency metadata if present
        body = args["body"]
        if depends_on:
            deps_ref = ", ".join(f"#{d}" for d in depends_on)
            body = f"{body}\n\nDependencies: {deps_ref}"

        # Create the issue
        labels = ["status:pending", f"priority:{priority}"]
        issue = await client.create_issue(args["title"], body, labels)
        issue_number = issue["number"]

        # Get node IDs
        epic_node_id = await client.get_issue_node_id(args["epic_number"])
        sub_issue_node_id = await client.get_issue_node_id(issue_number)

        # Link as sub-issue
        await client.add_sub_issue(epic_node_id, sub_issue_node_id)

        result: dict[str, Any] = {
            "number": issue_number,
            "title": args["title"],
            "url": issue["html_url"],
            "message": f"Created sub-issue #{issue_number} and linked to epic #{args['epic_number']}",
        }
        if depends_on:
            result["depends_on"] = depends_on

        return _json_success(result)
    except GitHubClientError as e:
        return _error(str(e))


async def impl_close_epic(args: dict[str, Any]) -> dict[str, Any]:
    """Close the epic issue."""
    try:
        client = _get_client()
        await client.close_issue(args["epic_number"])
        return _success(f"Closed epic #{args['epic_number']}")
    except GitHubClientError as e:
        return _error(str(e))


async def impl_get_pr_status(args: dict[str, Any]) -> dict[str, Any]:
    """Get the status of a pull request."""
    try:
        client = _get_client()
        pr_status = await client.get_pr_status(args["pr_number"])
        return _json_success(pr_status)
    except GitHubClientError as e:
        return _error(str(e))


async def impl_list_my_prs(args: dict[str, Any]) -> dict[str, Any]:
    """List pull requests created by a specific author."""
    try:
        client = _get_client()
        prs = await client.list_prs(state="open")

        # Filter by assignee label (assignee:agent-name)
        author_slug = args["author_name"].lower().replace(" ", "-")
        my_prs = []
        for pr in prs:
            pr_status = await client.get_pr_status(pr["number"])
            # Check if assignee label matches
            if pr_status.assignee and pr_status.assignee.lower() == author_slug:
                my_prs.append(pr_status)

        return _json_success(my_prs)
    except GitHubClientError as e:
        return _error(str(e))


async def impl_list_my_reviews(args: dict[str, Any]) -> dict[str, Any]:
    """List pull requests assigned to a specific reviewer that need action.

    Excludes PRs that have already been approved (is_approved=True
    with no pending feedback), since those are just waiting for the
    programmer to merge.
    """
    try:
        client = _get_client()
        prs = await client.list_prs(state="open")

        # Filter by reviewer label (reviewer:agent-name)
        reviewer_slug = args["reviewer_name"].lower().replace(" ", "-")
        my_reviews = []
        for pr in prs:
            pr_status = await client.get_pr_status(pr["number"])
            # Check if reviewer label matches
            if pr_status.reviewer and pr_status.reviewer.lower() == reviewer_slug:
                # Skip PRs already approved with no pending feedback —
                # these are just waiting for the programmer to merge
                if pr_status.is_approved and not pr_status.has_feedback:
                    continue
                my_reviews.append(pr_status)

        return _json_success(my_reviews)
    except GitHubClientError as e:
        return _error(str(e))


async def impl_list_prs_for_review(args: dict[str, Any]) -> dict[str, Any]:
    """List pull requests awaiting review."""
    try:
        client = _get_client()

        # Get epic to know which issues are sub-issues
        epic_status = await client.get_epic_status(args["epic_number"])
        sub_issue_numbers = {si.number for si in epic_status.sub_issues}

        # Get all open PRs
        prs = await client.list_prs(state="open")

        # Filter to PRs that:
        # 1. Are linked to a sub-issue of this epic
        # 2. Have status:in-review label (or no reviewer assigned)
        # 3. Don't have a reviewer assigned
        available_prs = []
        for pr in prs:
            pr_status = await client.get_pr_status(pr["number"])

            # Check if linked to a sub-issue of this epic
            if pr_status.linked_issue not in sub_issue_numbers:
                continue

            # Check if available for review (no reviewer assigned)
            if pr_status.reviewer is None:
                available_prs.append(pr_status)

        return _json_success(available_prs)
    except GitHubClientError as e:
        return _error(str(e))


async def impl_claim_pr_review(args: dict[str, Any]) -> dict[str, Any]:
    """Claim a PR for review by adding reviewer label."""
    try:
        client = _get_client()

        # Check if PR already has a reviewer
        pr_status = await client.get_pr_status(args["pr_number"])
        if pr_status.reviewer is not None:
            return _error(
                f"PR #{args['pr_number']} is already assigned to reviewer {pr_status.reviewer}"
            )

        # Convert reviewer name to slug
        reviewer_slug = args["reviewer_name"].lower().replace(" ", "-")

        # Add reviewer label
        await client.update_issue_labels(
            args["pr_number"],
            add_labels=[f"reviewer:{reviewer_slug}"],
        )

        return _success(
            f"Successfully claimed PR #{args['pr_number']} for review by {args['reviewer_name']}"
        )
    except GitHubClientError as e:
        return _error(str(e))


async def impl_request_changes(args: dict[str, Any]) -> dict[str, Any]:
    """Request changes on a PR.

    Adds the `status:feedback-requested` label and removes `status:approved`
    if present (in case of a re-review cycle where the reviewer previously
    approved but now requests changes).
    """
    try:
        client = _get_client()

        # Add feedback-requested label, remove approved if present
        await client.update_issue_labels(
            args["pr_number"],
            add_labels=["status:feedback-requested"],
            remove_labels=["status:approved"],
        )

        # Attempt to create a review with REQUEST_CHANGES (may not persist for self-reviews)
        try:
            await client.create_pr_review(
                args["pr_number"],
                event="REQUEST_CHANGES",
                body=args["comment"],
            )
        except GitHubClientError:
            # Self-reviews may fail — the label is the source of truth
            pass

        return _success(
            f"Requested changes on PR #{args['pr_number']} and added feedback-requested label"
        )
    except GitHubClientError as e:
        return _error(str(e))


async def impl_mark_feedback_addressed(args: dict[str, Any]) -> dict[str, Any]:
    """Mark that feedback has been addressed."""
    try:
        client = _get_client()

        # Remove feedback-requested label
        await client.update_issue_labels(
            args["pr_number"],
            remove_labels=["status:feedback-requested"],
        )

        # Add a comment noting feedback was addressed
        await client.create_issue_comment(
            args["pr_number"],
            "Feedback has been addressed. Ready for re-review.",
        )

        return _success(f"Marked feedback addressed on PR #{args['pr_number']}")
    except GitHubClientError as e:
        return _error(str(e))


async def impl_approve_pr(args: dict[str, Any]) -> dict[str, Any]:
    """Approve a pull request.

    Adds the `status:approved` label and removes `status:feedback-requested`
    if present. Also attempts to create a GitHub APPROVE review (may be
    silently ignored for self-reviews since all agents share one account).
    """
    try:
        client = _get_client()

        # Add approved label, remove feedback-requested if present
        await client.update_issue_labels(
            args["pr_number"],
            add_labels=["status:approved"],
            remove_labels=["status:feedback-requested"],
        )

        # Attempt to create an approval review (may not persist for self-reviews)
        comment = args.get("comment", "LGTM!")
        try:
            await client.create_pr_review(
                args["pr_number"],
                event="APPROVE",
                body=comment,
            )
        except GitHubClientError:
            # Self-reviews may fail — the label is the source of truth
            pass

        return _success(f"Approved PR #{args['pr_number']}")
    except GitHubClientError as e:
        return _error(str(e))


async def impl_log_activity(args: dict[str, Any]) -> dict[str, Any]:
    """Post an activity log comment on the epic issue.

    Event types: started, claimed, progress, pr_created, review_started,
                 review_submitted, feedback_addressed, merged, completed, idle, error
    """
    try:
        client = _get_client()

        # Format the activity comment
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        agent_type_display = args["agent_type"].capitalize()

        lines = [
            f"**[{timestamp}] {args['agent_name']}** ({agent_type_display})",
            "",
            f"**Event:** `{args['event_type']}`",
        ]

        # Add issue/PR references if provided
        issue_num = args.get("issue_number")
        pr_num = args.get("pr_number")

        if issue_num:
            lines.append(f"**Issue:** #{issue_num}")
        if pr_num:
            lines.append(f"**PR:** #{pr_num}")

        lines.append(f"**Message:** {args['message']}")

        comment_body = "\n".join(lines)

        # Post the comment on the epic
        comment = await client.create_issue_comment(args["epic_number"], comment_body)

        return _json_success(
            {
                "comment_id": comment["id"],
                "url": comment["html_url"],
                "message": "Activity logged successfully",
            }
        )
    except GitHubClientError as e:
        return _error(str(e))


async def impl_get_activity_log(args: dict[str, Any]) -> dict[str, Any]:
    """Get recent activity log entries from the epic."""
    try:
        client = _get_client()

        limit = args.get("limit", 20)
        comments = await client.list_issue_comments(args["epic_number"], per_page=limit)

        # Parse activity entries from comments
        activities = []
        for comment in reversed(comments):  # Most recent last
            entry = await client.parse_activity_comment(comment)
            if entry:
                activities.append(entry)

        return _json_success(activities)
    except GitHubClientError as e:
        return _error(str(e))


# =============================================================================
# MCP Tool Wrappers
# =============================================================================


@tool(
    "get_epic_status",
    "Get the status of an epic with all its sub-issues",
    {"epic_number": int},
)
async def tool_get_epic_status(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_get_epic_status(args)


@tool(
    "get_sub_issue",
    "Get detailed status of a specific sub-issue within an epic",
    {"epic_number": int, "sub_issue_number": int},
)
async def tool_get_sub_issue(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_get_sub_issue(args)


@tool(
    "list_available_sub_issues",
    "List unassigned pending sub-issues from an epic",
    {"epic_number": int, "priority": str},
)
async def tool_list_available_sub_issues(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_list_available_sub_issues(args)


@tool(
    "list_my_sub_issues",
    "List sub-issues assigned to a specific agent",
    {"epic_number": int, "agent_name": str},
)
async def tool_list_my_sub_issues(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_list_my_sub_issues(args)


@tool(
    "claim_sub_issue",
    "Claim an unassigned sub-issue by adding assignee label",
    {"epic_number": int, "sub_issue_number": int, "agent_name": str},
)
async def tool_claim_sub_issue(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_claim_sub_issue(args)


@tool(
    "update_sub_issue_status",
    "Update the status label on a sub-issue",
    {"epic_number": int, "sub_issue_number": int, "new_status": str},
)
async def tool_update_sub_issue_status(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_update_sub_issue_status(args)


@tool(
    "create_sub_issue",
    "Create a new sub-issue and link it to the epic. Use depends_on to specify issue numbers this task depends on (comma-separated, e.g. '3,5'). Leave empty for no dependencies.",
    {"epic_number": int, "title": str, "body": str, "priority": str, "depends_on": str},
)
async def tool_create_sub_issue(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_create_sub_issue(args)


@tool(
    "close_epic",
    "Close the epic issue",
    {"epic_number": int},
)
async def tool_close_epic(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_close_epic(args)


@tool(
    "get_pr_status",
    "Get the status of a pull request including feedback flag",
    {"pr_number": int},
)
async def tool_get_pr_status(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_get_pr_status(args)


@tool(
    "list_my_prs",
    "List pull requests created by a specific author",
    {"author_name": str},
)
async def tool_list_my_prs(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_list_my_prs(args)


@tool(
    "list_my_reviews",
    "List pull requests assigned to a specific reviewer",
    {"reviewer_name": str},
)
async def tool_list_my_reviews(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_list_my_reviews(args)


@tool(
    "list_prs_for_review",
    "List pull requests awaiting review (not assigned to a reviewer)",
    {"epic_number": int},
)
async def tool_list_prs_for_review(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_list_prs_for_review(args)


@tool(
    "claim_pr_review",
    "Claim a PR for review by adding reviewer label",
    {"pr_number": int, "reviewer_name": str},
)
async def tool_claim_pr_review(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_claim_pr_review(args)


@tool(
    "request_changes",
    "Request changes on a PR (adds feedback-requested label and posts comment)",
    {"pr_number": int, "comment": str},
)
async def tool_request_changes(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_request_changes(args)


@tool(
    "mark_feedback_addressed",
    "Mark that feedback has been addressed (removes feedback-requested label)",
    {"pr_number": int},
)
async def tool_mark_feedback_addressed(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_mark_feedback_addressed(args)


@tool(
    "approve_pr",
    "Approve a pull request",
    {"pr_number": int, "comment": str},
)
async def tool_approve_pr(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_approve_pr(args)


@tool(
    "log_activity",
    "Post an activity log comment on the epic issue",
    {
        "epic_number": int,
        "agent_name": str,
        "agent_type": str,
        "event_type": str,
        "message": str,
        "issue_number": int,
        "pr_number": int,
    },
)
async def tool_log_activity(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_log_activity(args)


@tool(
    "get_activity_log",
    "Get recent activity log entries from the epic",
    {"epic_number": int, "limit": int},
)
async def tool_get_activity_log(args: dict[str, Any]) -> dict[str, Any]:
    return await impl_get_activity_log(args)


# =============================================================================
# Server Factory
# =============================================================================


def create_orchestrator_server(
    name: str,
    github_repo: str,
) -> Any:
    """Create an MCP orchestration server.

    Args:
        name: Server name (used as prefix in tool names).
        github_repo: GitHub repository in OWNER/REPO format.

    Returns:
        McpSdkServerConfig to pass to ClaudeAgentOptions.mcp_servers.
    """
    global _github_client

    # Parse owner/repo
    parts = github_repo.split("/")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid github_repo format: {github_repo}. Expected OWNER/REPO"
        )
    owner, repo = parts

    # Initialize the GitHub client
    _github_client = GitHubClient(owner, repo)

    # Create the MCP server with all tools
    return create_sdk_mcp_server(
        name=name,
        version="1.0.0",
        tools=[
            # Epic/Issue tools
            tool_get_epic_status,
            tool_get_sub_issue,
            tool_list_available_sub_issues,
            tool_list_my_sub_issues,
            tool_claim_sub_issue,
            tool_update_sub_issue_status,
            tool_create_sub_issue,
            tool_close_epic,
            # PR tools
            tool_get_pr_status,
            tool_list_my_prs,
            tool_list_prs_for_review,
            tool_claim_pr_review,
            tool_request_changes,
            tool_mark_feedback_addressed,
            tool_approve_pr,
            # Activity tools
            tool_log_activity,
            tool_get_activity_log,
        ],
    )
