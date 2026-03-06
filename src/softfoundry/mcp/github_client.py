"""GitHub API client for the MCP orchestration package.

This module provides an async HTTP client that uses the GitHub REST and GraphQL APIs.
Authentication is handled via the `gh` CLI token.
"""

import re
import subprocess
from datetime import datetime
from typing import Any

import httpx

from softfoundry.mcp.types import (
    ActivityEntry,
    EpicStatus,
    PRStatus,
    SubIssueStatus,
)


class GitHubClientError(Exception):
    """Base exception for GitHub client errors."""

    pass


class GitHubClient:
    """Async GitHub API client.

    Uses httpx for async HTTP requests and authenticates via `gh auth token`.
    """

    def __init__(self, owner: str, repo: str):
        """Initialize the GitHub client.

        Args:
            owner: Repository owner (user or organization).
            repo: Repository name.
        """
        self.owner = owner
        self.repo = repo
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None

    @property
    def token(self) -> str:
        """Get the GitHub token, fetching from gh CLI if needed."""
        if self._token is None:
            self._token = self._get_gh_token()
        return self._token

    def _get_gh_token(self) -> str:
        """Get the GitHub token from the gh CLI."""
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise GitHubClientError(
                f"Failed to get GitHub token from gh CLI: {e.stderr}"
            ) from e
        except FileNotFoundError:
            raise GitHubClientError(
                "gh CLI not found. Please install GitHub CLI: https://cli.github.com/"
            )

    @property
    def client(self) -> httpx.AsyncClient:
        """Get the async HTTP client, creating if needed."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url="https://api.github.com",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Execute a GraphQL query.

        Args:
            query: The GraphQL query string.
            variables: Query variables.

        Returns:
            The response data.

        Raises:
            GitHubClientError: If the request fails.
        """
        response = await self.client.post(
            "/graphql",
            json={"query": query, "variables": variables},
        )
        if response.status_code != 200:
            raise GitHubClientError(
                f"GraphQL request failed: {response.status_code} {response.text}"
            )
        data = response.json()
        if "errors" in data:
            raise GitHubClientError(f"GraphQL errors: {data['errors']}")
        return data["data"]

    async def _rest_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Execute a REST GET request.

        Args:
            path: The API path (without base URL).
            params: Query parameters.

        Returns:
            The response JSON.

        Raises:
            GitHubClientError: If the request fails.
        """
        response = await self.client.get(path, params=params)
        if response.status_code != 200:
            raise GitHubClientError(
                f"REST GET {path} failed: {response.status_code} {response.text}"
            )
        return response.json()

    async def _rest_post(self, path: str, json: dict[str, Any]) -> Any:
        """Execute a REST POST request."""
        response = await self.client.post(path, json=json)
        if response.status_code not in (200, 201):
            raise GitHubClientError(
                f"REST POST {path} failed: {response.status_code} {response.text}"
            )
        return response.json()

    async def _rest_patch(self, path: str, json: dict[str, Any]) -> Any:
        """Execute a REST PATCH request."""
        response = await self.client.patch(path, json=json)
        if response.status_code != 200:
            raise GitHubClientError(
                f"REST PATCH {path} failed: {response.status_code} {response.text}"
            )
        return response.json()

    # Not used for now, but keeping it in case we need it later.
    async def _rest_put(self, path: str, json: dict[str, Any]) -> Any:
        """Execute a REST PUT request."""
        response = await self.client.put(path, json=json)
        if response.status_code not in (200, 201):
            raise GitHubClientError(
                f"REST PUT {path} failed: {response.status_code} {response.text}"
            )
        return response.json()

    # Not used for now, but keeping it in case we need it later.
    async def _rest_delete(self, path: str) -> None:
        """Execute a REST DELETE request."""
        response = await self.client.delete(path)
        if response.status_code not in (200, 204):
            raise GitHubClientError(
                f"REST DELETE {path} failed: {response.status_code} {response.text}"
            )

    # -------------------------------------------------------------------------
    # Issue Methods
    # -------------------------------------------------------------------------

    async def get_issue(self, issue_number: int) -> dict[str, Any]:
        """Get an issue by number."""
        return await self._rest_get(
            f"/repos/{self.owner}/{self.repo}/issues/{issue_number}"
        )

    async def get_issue_node_id(self, issue_number: int) -> str:
        """Get the GraphQL node ID for an issue."""
        query = """
        query GetIssueNodeId($owner: String!, $repo: String!, $number: Int!) {
            repository(owner: $owner, name: $repo) {
                issue(number: $number) {
                    id
                }
            }
        }
        """
        data = await self._graphql(
            query,
            {"owner": self.owner, "repo": self.repo, "number": issue_number},
        )
        return data["repository"]["issue"]["id"]

    async def get_sub_issues(self, parent_issue_number: int) -> list[dict[str, Any]]:
        """Get all sub-issues of a parent issue using GraphQL."""
        query = """
        query ListSubIssues($owner: String!, $repo: String!, $number: Int!) {
            repository(owner: $owner, name: $repo) {
                issue(number: $number) {
                    subIssues(first: 100) {
                        nodes {
                            number
                            title
                            state
                            body
                            labels(first: 10) {
                                nodes {
                                    name
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        data = await self._graphql(
            query,
            {"owner": self.owner, "repo": self.repo, "number": parent_issue_number},
        )
        return data["repository"]["issue"]["subIssues"]["nodes"]

    async def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new issue."""
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return await self._rest_post(
            f"/repos/{self.owner}/{self.repo}/issues",
            payload,
        )

    async def add_sub_issue(
        self, parent_node_id: str, sub_issue_node_id: str
    ) -> dict[str, Any]:
        """Link a sub-issue to a parent issue."""
        mutation = """
        mutation AddSubIssue($parentId: ID!, $subIssueId: ID!) {
            addSubIssue(input: {issueId: $parentId, subIssueId: $subIssueId}) {
                issue {
                    number
                    title
                }
                subIssue {
                    number
                    title
                }
            }
        }
        """
        data = await self._graphql(
            mutation,
            {"parentId": parent_node_id, "subIssueId": sub_issue_node_id},
        )
        return data["addSubIssue"]

    async def update_issue_labels(
        self,
        issue_number: int,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update labels on an issue.

        Args:
            issue_number: The issue number.
            add_labels: Labels to add.
            remove_labels: Labels to remove.

        Returns:
            The updated issue data.
        """
        # First get current labels
        issue = await self.get_issue(issue_number)
        current_labels = [label["name"] for label in issue.get("labels", [])]

        # Calculate new label set
        new_labels = set(current_labels)
        if remove_labels:
            new_labels -= set(remove_labels)
        if add_labels:
            new_labels |= set(add_labels)

        # Update labels
        return await self._rest_patch(
            f"/repos/{self.owner}/{self.repo}/issues/{issue_number}",
            {"labels": list(new_labels)},
        )

    async def close_issue(self, issue_number: int) -> dict[str, Any]:
        """Close an issue."""
        return await self._rest_patch(
            f"/repos/{self.owner}/{self.repo}/issues/{issue_number}",
            {"state": "closed"},
        )

    # -------------------------------------------------------------------------
    # PR Methods
    # -------------------------------------------------------------------------

    async def get_pr(self, pr_number: int) -> dict[str, Any]:
        """Get a pull request by number."""
        return await self._rest_get(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        )

    async def list_prs(
        self, state: str = "open", per_page: int = 30
    ) -> list[dict[str, Any]]:
        """List pull requests."""
        return await self._rest_get(
            f"/repos/{self.owner}/{self.repo}/pulls",
            params={"state": state, "per_page": per_page},
        )

    async def get_pr_reviews(self, pr_number: int) -> list[dict[str, Any]]:
        """Get reviews for a pull request."""
        return await self._rest_get(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/reviews"
        )

    async def create_pr_review(
        self,
        pr_number: int,
        event: str,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Create a review on a pull request.

        Args:
            pr_number: The PR number.
            event: Review event (APPROVE, REQUEST_CHANGES, COMMENT).
            body: Review comment body.

        Returns:
            The created review data.
        """
        payload: dict[str, Any] = {"event": event}
        if body:
            payload["body"] = body
        return await self._rest_post(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/reviews",
            payload,
        )

    async def create_pr_review_with_comments(
        self,
        pr_number: int,
        body: str,
        inline_comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a COMMENT review with inline diff-level comments.

        Uses event=COMMENT which works on self-reviews (unlike REQUEST_CHANGES
        or APPROVE which GitHub rejects when reviewing your own PR).

        Args:
            pr_number: The PR number.
            body: Top-level review body.
            inline_comments: List of dicts with keys: path, line, body.

        Returns:
            The created review data.
        """
        # Get the HEAD commit SHA (required for inline comments)
        pr = await self.get_pr(pr_number)
        commit_id = pr["head"]["sha"]

        payload: dict[str, Any] = {
            "event": "COMMENT",
            "body": body,
            "commit_id": commit_id,
            "comments": inline_comments,
        }
        return await self._rest_post(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/reviews",
            payload,
        )

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a pull request.

        Args:
            title: PR title.
            body: PR body/description.
            head: The branch containing changes.
            base: The branch to merge into (default: main).
            labels: Labels to add to the PR.

        Returns:
            The created PR data.
        """
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }
        pr = await self._rest_post(
            f"/repos/{self.owner}/{self.repo}/pulls",
            payload,
        )

        # Add labels if provided (PRs use the issues API for labels)
        if labels:
            await self.update_issue_labels(pr["number"], add_labels=labels)

        return pr

    async def merge_pr(
        self,
        pr_number: int,
        method: str = "squash",
        delete_branch: bool = True,
    ) -> dict[str, Any]:
        """Merge a pull request.

        Args:
            pr_number: The PR number.
            method: Merge method (merge, squash, rebase). Default: squash.
            delete_branch: Whether to delete the head branch after merge.

        Returns:
            The merge result data.
        """
        result = await self._rest_put(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/merge",
            {"merge_method": method},
        )

        if delete_branch:
            # Get the PR to find the head branch
            pr = await self.get_pr(pr_number)
            head_ref = pr["head"]["ref"]
            try:
                await self._rest_delete(
                    f"/repos/{self.owner}/{self.repo}/git/refs/heads/{head_ref}"
                )
            except GitHubClientError:
                # Branch may already be deleted or protected
                pass

        return result

    async def get_pr_review_comments(self, pr_number: int) -> list[dict[str, Any]]:
        """Get inline review comments on a PR (diff-level comments).

        These are comments attached to specific lines in the diff,
        distinct from issue-level comments.

        Args:
            pr_number: The PR number.

        Returns:
            List of review comment dicts with path, line, body, etc.
        """
        return await self._rest_get(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/comments"
        )

    async def get_pr_diff(self, pr_number: int) -> str:
        """Get the diff text of a pull request.

        Args:
            pr_number: The PR number.

        Returns:
            The diff as a string.
        """
        response = await self.client.get(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        if response.status_code != 200:
            raise GitHubClientError(
                f"Failed to get PR diff: {response.status_code} {response.text}"
            )
        return response.text

    async def list_issues_by_labels(
        self,
        labels: str,
        state: str = "open",
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        """List issues filtered by labels.

        Args:
            labels: Comma-separated label names.
            state: Issue state (open, closed, all).
            per_page: Results per page.

        Returns:
            List of issue dicts.
        """
        return await self._rest_get(
            f"/repos/{self.owner}/{self.repo}/issues",
            params={"labels": labels, "state": state, "per_page": per_page},
        )

    # -------------------------------------------------------------------------
    # Comment Methods
    # -------------------------------------------------------------------------

    async def create_issue_comment(
        self, issue_number: int, body: str
    ) -> dict[str, Any]:
        """Create a comment on an issue."""
        return await self._rest_post(
            f"/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments",
            {"body": body},
        )

    async def list_issue_comments(
        self, issue_number: int, per_page: int = 30
    ) -> list[dict[str, Any]]:
        """List comments on an issue."""
        return await self._rest_get(
            f"/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments",
            params={"per_page": per_page},
        )

    # -------------------------------------------------------------------------
    # Label Methods
    # -------------------------------------------------------------------------

    async def create_label(
        self, name: str, color: str, description: str = ""
    ) -> dict[str, Any]:
        """Create a label (or update if exists)."""
        try:
            return await self._rest_post(
                f"/repos/{self.owner}/{self.repo}/labels",
                {"name": name, "color": color, "description": description},
            )
        except GitHubClientError as e:
            if "already_exists" in str(e):
                # Update existing label
                return await self._rest_patch(
                    f"/repos/{self.owner}/{self.repo}/labels/{name}",
                    {"color": color, "description": description},
                )
            raise

    # -------------------------------------------------------------------------
    # High-Level Methods (return typed objects)
    # -------------------------------------------------------------------------

    def _parse_labels(
        self, labels: list[dict[str, Any]]
    ) -> tuple[str | None, str | None, str | None, bool, bool]:
        """Parse labels to extract status, assignee, priority, and review flags.

        Returns:
            Tuple of (status, assignee, priority, has_feedback, is_approved)
        """
        status = None
        assignee = None
        priority = None
        has_feedback = False
        is_approved = False

        for label in labels:
            name = label.get("name", "")
            if name.startswith("status:"):
                status_value = name.split(":", 1)[1]
                if status_value == "feedback-requested":
                    has_feedback = True
                elif status_value == "approved":
                    is_approved = True
                else:
                    status = status_value
            elif name.startswith("assignee:"):
                assignee = name.split(":", 1)[1]
            elif name.startswith("priority:"):
                priority = name.split(":", 1)[1]
            elif name.startswith("reviewer:"):
                # For PRs, we handle reviewer separately
                pass

        return status, assignee, priority, has_feedback, is_approved

    def _parse_reviewer_label(self, labels: list[dict[str, Any]]) -> str | None:
        """Parse labels to extract reviewer."""
        for label in labels:
            name = label.get("name", "")
            if name.startswith("reviewer:"):
                return name.split(":", 1)[1]
        return None

    def _extract_linked_issue(self, body: str | None) -> int | None:
        """Extract linked issue number from PR body (Closes #N, Fixes #N)."""
        if not body:
            return None
        match = re.search(r"(?:closes|fixes|resolves)\s+#(\d+)", body, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _parse_dependencies(self, body: str | None) -> list[int]:
        """Parse dependency issue numbers from an issue body.

        Looks for a line like: Dependencies: #3, #5, #7

        Returns:
            List of issue numbers this issue depends on.
        """
        if not body:
            return []
        match = re.search(r"^Dependencies:\s*(.+)$", body, re.MULTILINE)
        if not match:
            return []
        deps_str = match.group(1)
        return [int(m.group(1)) for m in re.finditer(r"#(\d+)", deps_str)]

    async def get_epic_status(self, epic_number: int) -> EpicStatus:
        """Get the status of an epic with all its sub-issues.

        Cross-references open PRs to populate linked_pr and reviewer fields
        on sub-issues.

        Args:
            epic_number: The epic issue number.

        Returns:
            EpicStatus with all sub-issue information.
        """
        # Get the epic issue
        epic = await self.get_issue(epic_number)

        # Get sub-issues via GraphQL
        sub_issues_data = await self.get_sub_issues(epic_number)

        # Build a map of issue_number -> (pr_number, reviewer) from open PRs
        pr_link_map: dict[int, tuple[int, str | None]] = {}
        try:
            open_prs = await self.list_prs(state="open")
            for pr in open_prs:
                linked_issue = self._extract_linked_issue(pr.get("body"))
                if linked_issue is not None:
                    reviewer = self._parse_reviewer_label(pr.get("labels", []))
                    pr_link_map[linked_issue] = (pr["number"], reviewer)
        except GitHubClientError:
            # If PR listing fails, continue without linked PR data
            pass

        # Also check merged/closed PRs that may have already been linked
        try:
            closed_prs = await self.list_prs(state="closed")
            for pr in closed_prs:
                linked_issue = self._extract_linked_issue(pr.get("body"))
                if linked_issue is not None and linked_issue not in pr_link_map:
                    reviewer = self._parse_reviewer_label(pr.get("labels", []))
                    pr_link_map[linked_issue] = (pr["number"], reviewer)
        except GitHubClientError:
            pass

        # Convert to SubIssueStatus objects
        sub_issues = []
        completed = 0
        for si in sub_issues_data:
            labels = si.get("labels", {}).get("nodes", [])
            status, assignee, priority, _, _ = self._parse_labels(labels)
            depends_on = self._parse_dependencies(si.get("body"))

            state = si["state"].lower()

            # Normalize sf_status: when issue is closed, ignore stale labels
            sf_status = None if state == "closed" else status

            # Look up linked PR and reviewer
            linked_pr = None
            reviewer = None
            if si["number"] in pr_link_map:
                linked_pr, reviewer = pr_link_map[si["number"]]

            sub_issue = SubIssueStatus(
                number=si["number"],
                title=si["title"],
                state=state,
                sf_status=sf_status,
                assignee=assignee,
                reviewer=reviewer,
                priority=priority,
                linked_pr=linked_pr,
                depends_on=depends_on,
            )
            sub_issues.append(sub_issue)

            if state == "closed":
                completed += 1

        return EpicStatus(
            number=epic["number"],
            title=epic["title"],
            state=epic["state"],
            body=epic.get("body", ""),
            sub_issues=sub_issues,
            total_sub_issues=len(sub_issues),
            completed_sub_issues=completed,
        )

    async def get_sub_issue_status(
        self, epic_number: int, sub_issue_number: int
    ) -> SubIssueStatus:
        """Get the status of a specific sub-issue.

        Cross-references open PRs to populate linked_pr and reviewer.

        Args:
            epic_number: The parent epic issue number.
            sub_issue_number: The sub-issue number.

        Returns:
            SubIssueStatus for the sub-issue.

        Raises:
            GitHubClientError: If the sub-issue is not part of the epic.
        """
        # Get sub-issues and find the specific one
        sub_issues = await self.get_sub_issues(epic_number)

        for si in sub_issues:
            if si["number"] == sub_issue_number:
                labels = si.get("labels", {}).get("nodes", [])
                status, assignee, priority, _, _ = self._parse_labels(labels)
                depends_on = self._parse_dependencies(si.get("body"))

                state = si["state"].lower()
                sf_status = None if state == "closed" else status

                # Look up linked PR and reviewer
                linked_pr = None
                reviewer = None
                try:
                    for pr_state in ("open", "closed"):
                        prs = await self.list_prs(state=pr_state)
                        for pr in prs:
                            linked = self._extract_linked_issue(pr.get("body"))
                            if linked == sub_issue_number:
                                linked_pr = pr["number"]
                                reviewer = self._parse_reviewer_label(
                                    pr.get("labels", [])
                                )
                                break
                        if linked_pr is not None:
                            break
                except GitHubClientError:
                    pass

                return SubIssueStatus(
                    number=si["number"],
                    title=si["title"],
                    state=state,
                    sf_status=sf_status,
                    assignee=assignee,
                    reviewer=reviewer,
                    priority=priority,
                    linked_pr=linked_pr,
                    depends_on=depends_on,
                )

        raise GitHubClientError(
            f"Sub-issue #{sub_issue_number} is not part of epic #{epic_number}"
        )

    async def get_pr_status(self, pr_number: int) -> PRStatus:
        """Get the status of a pull request.

        Args:
            pr_number: The PR number.

        Returns:
            PRStatus with full PR information.
        """
        pr = await self.get_pr(pr_number)
        labels = pr.get("labels", [])

        # Parse labels — approval and feedback are label-driven because
        # GitHub does not support self-reviews (all agents share one account)
        _, assignee, _, has_feedback, is_approved = self._parse_labels(labels)
        reviewer = self._parse_reviewer_label(labels)

        # Check for merge conflicts
        mergeable = pr.get("mergeable", True)
        has_conflicts = pr.get("mergeable_state") == "dirty"

        # Extract linked issue
        linked_issue = self._extract_linked_issue(pr.get("body"))

        return PRStatus(
            number=pr["number"],
            title=pr["title"],
            state="merged" if pr.get("merged") else pr["state"],
            assignee=assignee,
            reviewer=reviewer,
            has_feedback=has_feedback,
            is_approved=is_approved,
            mergeable=mergeable if mergeable is not None else True,
            has_conflicts=has_conflicts,
            linked_issue=linked_issue,
            head_branch=pr["head"]["ref"],
            base_branch=pr["base"]["ref"],
        )

    async def parse_activity_comment(
        self, comment: dict[str, Any]
    ) -> ActivityEntry | None:
        """Parse an activity log comment.

        Activity comments follow a specific format:
        **[TIMESTAMP] Agent Name** (Type)
        **Event:** `event_type`
        ...

        Returns:
            ActivityEntry if the comment is a valid activity log, None otherwise.
        """
        body = comment.get("body", "")

        # Try to parse the activity format
        # **[2026-02-27 10:30 UTC] Alice Chen** (Programmer)
        header_match = re.match(
            r"\*\*\[(.+?)\]\s+(.+?)\*\*\s+\((\w+)\)",
            body,
        )
        if not header_match:
            return None

        timestamp_str, agent_name, agent_type = header_match.groups()

        # Parse event type
        event_match = re.search(r"\*\*Event:\*\*\s+`(\w+)`", body)
        event_type = event_match.group(1) if event_match else "progress"

        # Parse issue number
        issue_match = re.search(r"\*\*Issue:\*\*\s+#(\d+)", body)
        issue_number = int(issue_match.group(1)) if issue_match else None

        # Parse PR number
        pr_match = re.search(r"\*\*PR:\*\*\s+#(\d+)", body)
        pr_number = int(pr_match.group(1)) if pr_match else None

        # Parse message
        message_match = re.search(r"\*\*Message:\*\*\s+(.+?)(?:\n|$)", body)
        message = message_match.group(1) if message_match else ""

        # Parse timestamp
        try:
            # Try parsing the timestamp (format: 2026-02-27 10:30 UTC)
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M %Z")
        except ValueError:
            timestamp = datetime.fromisoformat(
                comment["created_at"].replace("Z", "+00:00")
            )

        return ActivityEntry(
            timestamp=timestamp,
            agent_name=agent_name,
            agent_type=agent_type.lower(),
            event_type=event_type,
            message=message,
            issue_number=issue_number,
            pr_number=pr_number,
            comment_id=comment["id"],
        )
