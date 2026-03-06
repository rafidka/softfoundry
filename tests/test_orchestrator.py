"""Integration tests for the MCP orchestrator tools.

This module tests all MCP orchestrator tools with real GitHub API calls.
Tests are organized by:
- Individual tool tests (Epic, PR, Activity)
- Workflow tests (Sub-issue lifecycle, PR review lifecycle)
- Race condition tests
- End-to-end workflow tests

All tests use the integration test repository and clean up after themselves.
"""

import json
import time
from typing import Any

import pytest
import pytest_asyncio

from softfoundry.mcp.github_client import GitHubClient
from softfoundry.mcp.constants import DEFAULT_GITHUB_REPO
from softfoundry.mcp import orchestrator

# Integration test repository
INTEGRATION_TEST_REPO = DEFAULT_GITHUB_REPO


# =============================================================================
# Helper Functions
# =============================================================================


def parse_response(response: dict[str, Any]) -> Any:
    """Parse a tool response and return the data."""
    content = response.get("content", [])
    if not content:
        return None

    text = content[0].get("text", "")

    # Check for error
    if response.get("isError"):
        raise AssertionError(f"Tool returned error: {text}")

    # Try to parse as JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def expect_error(response: dict[str, Any], substring: str = "") -> str:
    """Assert that response is an error and optionally contains substring."""
    assert response.get("isError"), f"Expected error response, got: {response}"
    text = response["content"][0]["text"]
    if substring:
        assert substring in text, f"Expected '{substring}' in error: {text}"
    return text


# =============================================================================
# Fixtures
# =============================================================================


@pytest_asyncio.fixture
async def client():
    """Create a GitHubClient for direct API access in tests."""
    owner, repo = INTEGRATION_TEST_REPO.split("/")
    client = GitHubClient(owner, repo)
    yield client
    await client.close()


@pytest.fixture
def setup_orchestrator(client):
    """Initialize the orchestrator module with our test client."""
    # Set the module-level client
    orchestrator._github_client = client
    yield
    # Clean up
    orchestrator._github_client = None


@pytest_asyncio.fixture
async def fresh_epic(client, setup_orchestrator):
    """Create a fresh epic issue for testing. Cleans up after test."""
    timestamp = int(time.time())

    # Create epic issue
    epic = await client.create_issue(
        title=f"[Test Epic] Integration test {timestamp}",
        body="Epic created for orchestrator integration tests. Will be cleaned up.",
        labels=["type:epic"],
    )
    epic_number = epic["number"]

    # Track sub-issues and comments for cleanup
    created_sub_issues = []
    created_labels = []

    yield {
        "number": epic_number,
        "title": epic["title"],
        "timestamp": timestamp,
        "sub_issues": created_sub_issues,
        "labels": created_labels,
    }

    # Cleanup: Close epic and all sub-issues
    try:
        # Get all sub-issues
        sub_issues = await client.get_sub_issues(epic_number)
        for si in sub_issues:
            await client.close_issue(si["number"])

        # Close any tracked sub-issues (in case they weren't linked yet)
        for si_num in created_sub_issues:
            try:
                await client.close_issue(si_num)
            except Exception:
                pass  # Already closed or doesn't exist

        # Close the epic
        await client.close_issue(epic_number)
    except Exception as e:
        print(f"Cleanup warning: {e}")


@pytest_asyncio.fixture
async def fresh_pr(client, fresh_epic):
    """Create a fresh PR linked to the epic for testing. Cleans up after test."""
    timestamp = fresh_epic["timestamp"]
    epic_number = fresh_epic["number"]

    # Create a sub-issue for the PR to link to
    sub_issue = await client.create_issue(
        title=f"[Test] Sub-issue for PR test {timestamp}",
        body="Sub-issue that will have a PR linked to it.",
        labels=["status:pending"],
    )
    sub_issue_number = sub_issue["number"]
    fresh_epic["sub_issues"].append(sub_issue_number)

    # Link to epic
    epic_node_id = await client.get_issue_node_id(epic_number)
    sub_node_id = await client.get_issue_node_id(sub_issue_number)
    await client.add_sub_issue(epic_node_id, sub_node_id)

    # Create a branch
    branch_name = f"test-branch-{timestamp}"

    # Get the default branch SHA
    repo_info = await client._rest_get(f"/repos/{INTEGRATION_TEST_REPO}")
    default_branch = repo_info["default_branch"]
    ref = await client._rest_get(
        f"/repos/{INTEGRATION_TEST_REPO}/git/ref/heads/{default_branch}"
    )
    base_sha = ref["object"]["sha"]

    # Create a new branch
    await client._rest_post(
        f"/repos/{INTEGRATION_TEST_REPO}/git/refs",
        {"ref": f"refs/heads/{branch_name}", "sha": base_sha},
    )

    # Create a file in the branch
    file_content = f"# Test file\n\nCreated at {timestamp} for PR testing.\n"
    import base64

    await client._rest_put(
        f"/repos/{INTEGRATION_TEST_REPO}/contents/test-files/test-{timestamp}.md",
        {
            "message": f"Add test file for PR {timestamp}",
            "content": base64.b64encode(file_content.encode()).decode(),
            "branch": branch_name,
        },
    )

    # Create the PR
    pr = await client._rest_post(
        f"/repos/{INTEGRATION_TEST_REPO}/pulls",
        {
            "title": f"[Test PR] Integration test {timestamp}",
            "body": f"Closes #{sub_issue_number}\n\nTest PR for orchestrator integration tests.",
            "head": branch_name,
            "base": default_branch,
        },
    )
    pr_number = pr["number"]

    yield {
        "number": pr_number,
        "title": pr["title"],
        "branch": branch_name,
        "sub_issue_number": sub_issue_number,
        "epic_number": epic_number,
        "timestamp": timestamp,
    }

    # Cleanup: Close PR and delete branch
    try:
        # Close the PR
        await client._rest_patch(
            f"/repos/{INTEGRATION_TEST_REPO}/pulls/{pr_number}",
            {"state": "closed"},
        )

        # Delete the branch
        await client._rest_delete(
            f"/repos/{INTEGRATION_TEST_REPO}/git/refs/heads/{branch_name}"
        )
    except Exception as e:
        print(f"PR cleanup warning: {e}")


# =============================================================================
# TestEpicTools
# =============================================================================


@pytest.mark.slow
class TestEpicTools:
    """Tests for epic and sub-issue related tools."""

    async def test_get_epic_status_empty(self, fresh_epic, setup_orchestrator):
        """get_epic_status returns epic with no sub-issues."""
        response = await orchestrator.impl_get_epic_status(
            {"epic_number": fresh_epic["number"]}
        )
        data = parse_response(response)

        assert data["number"] == fresh_epic["number"]
        assert data["total_sub_issues"] == 0
        assert data["completed_sub_issues"] == 0
        assert data["sub_issues"] == []

    async def test_get_epic_status_with_sub_issues(
        self, client, fresh_epic, setup_orchestrator
    ):
        """get_epic_status returns epic with sub-issues."""
        epic_number = fresh_epic["number"]

        # Create sub-issues
        for i in range(2):
            result = await orchestrator.impl_create_sub_issue(
                {
                    "epic_number": epic_number,
                    "title": f"Sub-issue {i + 1}",
                    "body": f"Test sub-issue {i + 1}",
                    "priority": "medium",
                }
            )
            data = parse_response(result)
            fresh_epic["sub_issues"].append(data["number"])

        # Get epic status
        response = await orchestrator.impl_get_epic_status({"epic_number": epic_number})
        data = parse_response(response)

        assert data["number"] == epic_number
        assert data["total_sub_issues"] == 2
        assert data["completed_sub_issues"] == 0
        assert len(data["sub_issues"]) == 2

    async def test_get_sub_issue(self, client, fresh_epic, setup_orchestrator):
        """get_sub_issue returns sub-issue details."""
        epic_number = fresh_epic["number"]

        # Create a sub-issue
        result = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "Test sub-issue",
                "body": "Test body",
                "priority": "high",
            }
        )
        sub_data = parse_response(result)
        sub_number = sub_data["number"]
        fresh_epic["sub_issues"].append(sub_number)

        # Get sub-issue
        response = await orchestrator.impl_get_sub_issue(
            {"epic_number": epic_number, "sub_issue_number": sub_number}
        )
        data = parse_response(response)

        assert data["number"] == sub_number
        assert data["title"] == "Test sub-issue"
        assert data["priority"] == "high"
        assert data["sf_status"] == "pending"
        assert data["assignee"] is None

    async def test_get_sub_issue_not_found(self, fresh_epic, setup_orchestrator):
        """get_sub_issue returns error for non-existent sub-issue."""
        response = await orchestrator.impl_get_sub_issue(
            {"epic_number": fresh_epic["number"], "sub_issue_number": 999999}
        )
        expect_error(response, "not part of epic")

    async def test_create_sub_issue(self, fresh_epic, setup_orchestrator):
        """create_sub_issue creates and links a sub-issue."""
        epic_number = fresh_epic["number"]

        response = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "New sub-issue",
                "body": "Description here",
                "priority": "low",
            }
        )
        data = parse_response(response)

        assert "number" in data
        assert data["title"] == "New sub-issue"
        assert "linked to epic" in data["message"]
        fresh_epic["sub_issues"].append(data["number"])

        # Verify it appears in epic
        epic_response = await orchestrator.impl_get_epic_status(
            {"epic_number": epic_number}
        )
        epic_data = parse_response(epic_response)
        assert epic_data["total_sub_issues"] == 1

    async def test_create_sub_issue_with_priorities(
        self, fresh_epic, setup_orchestrator
    ):
        """create_sub_issue respects priority parameter."""
        epic_number = fresh_epic["number"]

        for priority in ["high", "medium", "low"]:
            response = await orchestrator.impl_create_sub_issue(
                {
                    "epic_number": epic_number,
                    "title": f"{priority.capitalize()} priority issue",
                    "body": "Test",
                    "priority": priority,
                }
            )
            data = parse_response(response)
            fresh_epic["sub_issues"].append(data["number"])

            # Verify priority
            sub_response = await orchestrator.impl_get_sub_issue(
                {"epic_number": epic_number, "sub_issue_number": data["number"]}
            )
            sub_data = parse_response(sub_response)
            assert sub_data["priority"] == priority

    async def test_list_available_sub_issues_all(self, fresh_epic, setup_orchestrator):
        """list_available_sub_issues returns all unassigned pending issues."""
        epic_number = fresh_epic["number"]

        # Create 3 sub-issues
        for i in range(3):
            result = await orchestrator.impl_create_sub_issue(
                {
                    "epic_number": epic_number,
                    "title": f"Available issue {i + 1}",
                    "body": "Test",
                    "priority": "medium",
                }
            )
            fresh_epic["sub_issues"].append(parse_response(result)["number"])

        # List available
        response = await orchestrator.impl_list_available_sub_issues(
            {"epic_number": epic_number, "priority": None}
        )
        data = parse_response(response)

        assert len(data) == 3

    async def test_list_available_sub_issues_by_priority(
        self, fresh_epic, setup_orchestrator
    ):
        """list_available_sub_issues filters by priority."""
        epic_number = fresh_epic["number"]

        # Create issues with different priorities
        priorities = ["high", "high", "medium", "low"]
        for i, priority in enumerate(priorities):
            result = await orchestrator.impl_create_sub_issue(
                {
                    "epic_number": epic_number,
                    "title": f"Issue {i + 1}",
                    "body": "Test",
                    "priority": priority,
                }
            )
            fresh_epic["sub_issues"].append(parse_response(result)["number"])

        # Filter by high priority
        response = await orchestrator.impl_list_available_sub_issues(
            {"epic_number": epic_number, "priority": "high"}
        )
        data = parse_response(response)

        assert len(data) == 2
        for issue in data:
            assert issue["priority"] == "high"

    async def test_list_available_sub_issues_excludes_assigned(
        self, fresh_epic, setup_orchestrator
    ):
        """list_available_sub_issues excludes assigned issues."""
        epic_number = fresh_epic["number"]

        # Create 2 sub-issues
        sub_numbers = []
        for i in range(2):
            result = await orchestrator.impl_create_sub_issue(
                {
                    "epic_number": epic_number,
                    "title": f"Issue {i + 1}",
                    "body": "Test",
                    "priority": "medium",
                }
            )
            num = parse_response(result)["number"]
            sub_numbers.append(num)
            fresh_epic["sub_issues"].append(num)

        # Claim first issue
        await orchestrator.impl_claim_sub_issue(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_numbers[0],
                "agent_name": "Test Agent",
            }
        )

        # List available - should only see 1
        response = await orchestrator.impl_list_available_sub_issues(
            {"epic_number": epic_number, "priority": None}
        )
        data = parse_response(response)

        assert len(data) == 1
        assert data[0]["number"] == sub_numbers[1]

    async def test_list_my_sub_issues(self, fresh_epic, setup_orchestrator):
        """list_my_sub_issues returns issues assigned to agent."""
        epic_number = fresh_epic["number"]

        # Create and claim 2 issues
        for i in range(2):
            result = await orchestrator.impl_create_sub_issue(
                {
                    "epic_number": epic_number,
                    "title": f"My issue {i + 1}",
                    "body": "Test",
                    "priority": "medium",
                }
            )
            num = parse_response(result)["number"]
            fresh_epic["sub_issues"].append(num)

            await orchestrator.impl_claim_sub_issue(
                {
                    "epic_number": epic_number,
                    "sub_issue_number": num,
                    "agent_name": "Alice Chen",
                }
            )

        # Create an issue for someone else
        result = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "Bob's issue",
                "body": "Test",
                "priority": "medium",
            }
        )
        bob_num = parse_response(result)["number"]
        fresh_epic["sub_issues"].append(bob_num)
        await orchestrator.impl_claim_sub_issue(
            {
                "epic_number": epic_number,
                "sub_issue_number": bob_num,
                "agent_name": "Bob Smith",
            }
        )

        # List Alice's issues
        response = await orchestrator.impl_list_my_sub_issues(
            {"epic_number": epic_number, "agent_name": "Alice Chen"}
        )
        data = parse_response(response)

        assert len(data) == 2
        for issue in data:
            assert issue["assignee"] == "alice-chen"

    async def test_list_my_sub_issues_empty(self, fresh_epic, setup_orchestrator):
        """list_my_sub_issues returns empty list when no assignments."""
        response = await orchestrator.impl_list_my_sub_issues(
            {"epic_number": fresh_epic["number"], "agent_name": "Nobody"}
        )
        data = parse_response(response)

        assert data == []

    async def test_claim_sub_issue(self, fresh_epic, setup_orchestrator):
        """claim_sub_issue assigns issue to agent."""
        epic_number = fresh_epic["number"]

        # Create sub-issue
        result = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "Claimable issue",
                "body": "Test",
                "priority": "medium",
            }
        )
        sub_number = parse_response(result)["number"]
        fresh_epic["sub_issues"].append(sub_number)

        # Claim it
        response = await orchestrator.impl_claim_sub_issue(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_number,
                "agent_name": "Test Agent",
            }
        )
        text = parse_response(response)
        assert "Successfully claimed" in text

        # Verify assignment
        sub_response = await orchestrator.impl_get_sub_issue(
            {"epic_number": epic_number, "sub_issue_number": sub_number}
        )
        sub_data = parse_response(sub_response)
        assert sub_data["assignee"] == "test-agent"
        assert sub_data["sf_status"] == "in-progress"

    async def test_claim_sub_issue_already_assigned(
        self, fresh_epic, setup_orchestrator
    ):
        """claim_sub_issue fails if already assigned."""
        epic_number = fresh_epic["number"]

        # Create and claim sub-issue
        result = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "Already claimed",
                "body": "Test",
                "priority": "medium",
            }
        )
        sub_number = parse_response(result)["number"]
        fresh_epic["sub_issues"].append(sub_number)

        await orchestrator.impl_claim_sub_issue(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_number,
                "agent_name": "First Agent",
            }
        )

        # Try to claim again
        response = await orchestrator.impl_claim_sub_issue(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_number,
                "agent_name": "Second Agent",
            }
        )
        expect_error(response, "already assigned")

    async def test_update_sub_issue_status_valid(self, fresh_epic, setup_orchestrator):
        """update_sub_issue_status changes status label."""
        epic_number = fresh_epic["number"]

        # Create sub-issue
        result = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "Status test",
                "body": "Test",
                "priority": "medium",
            }
        )
        sub_number = parse_response(result)["number"]
        fresh_epic["sub_issues"].append(sub_number)

        # Test each valid status transition
        for status in ["in-progress", "in-review", "pending"]:
            response = await orchestrator.impl_update_sub_issue_status(
                {
                    "epic_number": epic_number,
                    "sub_issue_number": sub_number,
                    "new_status": status,
                }
            )
            text = parse_response(response)
            assert f"status to {status}" in text

            # Verify status
            sub_response = await orchestrator.impl_get_sub_issue(
                {"epic_number": epic_number, "sub_issue_number": sub_number}
            )
            sub_data = parse_response(sub_response)
            assert sub_data["sf_status"] == status

    async def test_update_sub_issue_status_invalid(
        self, fresh_epic, setup_orchestrator
    ):
        """update_sub_issue_status rejects invalid status."""
        epic_number = fresh_epic["number"]

        # Create sub-issue
        result = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "Invalid status test",
                "body": "Test",
                "priority": "medium",
            }
        )
        sub_number = parse_response(result)["number"]
        fresh_epic["sub_issues"].append(sub_number)

        # Try invalid status
        response = await orchestrator.impl_update_sub_issue_status(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_number,
                "new_status": "invalid-status",
            }
        )
        expect_error(response, "Invalid status")

    async def test_close_epic(self, client, fresh_epic, setup_orchestrator):
        """close_epic closes the epic issue."""
        epic_number = fresh_epic["number"]

        response = await orchestrator.impl_close_epic({"epic_number": epic_number})
        text = parse_response(response)
        assert f"Closed epic #{epic_number}" in text

        # Verify closed
        issue = await client.get_issue(epic_number)
        assert issue["state"] == "closed"


# =============================================================================
# TestPRTools
# =============================================================================


@pytest.mark.slow
class TestPRTools:
    """Tests for pull request related tools."""

    async def test_get_pr_status(self, fresh_pr, setup_orchestrator):
        """get_pr_status returns PR details."""
        response = await orchestrator.impl_get_pr_status(
            {"pr_number": fresh_pr["number"]}
        )
        data = parse_response(response)

        assert data["number"] == fresh_pr["number"]
        assert data["state"] == "open"
        assert data["linked_issue"] == fresh_pr["sub_issue_number"]
        assert data["has_feedback"] is False
        assert data["reviewer"] is None

    async def test_get_pr_status_with_feedback_label(
        self, client, fresh_pr, setup_orchestrator
    ):
        """get_pr_status detects feedback-requested label."""
        pr_number = fresh_pr["number"]

        # Add feedback-requested label
        await client.update_issue_labels(
            pr_number, add_labels=["status:feedback-requested"]
        )

        response = await orchestrator.impl_get_pr_status({"pr_number": pr_number})
        data = parse_response(response)

        assert data["has_feedback"] is True

    async def test_list_prs_for_review_available(self, fresh_pr, setup_orchestrator):
        """list_prs_for_review shows PR without reviewer."""
        response = await orchestrator.impl_list_prs_for_review(
            {"epic_number": fresh_pr["epic_number"]}
        )
        data = parse_response(response)

        pr_numbers = [pr["number"] for pr in data]
        assert fresh_pr["number"] in pr_numbers

    async def test_list_prs_for_review_excludes_claimed(
        self, fresh_pr, setup_orchestrator
    ):
        """list_prs_for_review excludes PRs with reviewer."""
        pr_number = fresh_pr["number"]
        epic_number = fresh_pr["epic_number"]

        # Claim PR for review
        await orchestrator.impl_claim_pr_review(
            {"pr_number": pr_number, "reviewer_name": "Test Reviewer"}
        )

        # Should not appear in available list
        response = await orchestrator.impl_list_prs_for_review(
            {"epic_number": epic_number}
        )
        data = parse_response(response)

        pr_numbers = [pr["number"] for pr in data]
        assert pr_number not in pr_numbers

    async def test_claim_pr_review(self, fresh_pr, setup_orchestrator):
        """claim_pr_review assigns reviewer to PR."""
        pr_number = fresh_pr["number"]

        response = await orchestrator.impl_claim_pr_review(
            {"pr_number": pr_number, "reviewer_name": "Rachel Review"}
        )
        text = parse_response(response)
        assert "Successfully claimed" in text

        # Verify reviewer assigned
        status_response = await orchestrator.impl_get_pr_status(
            {"pr_number": pr_number}
        )
        status_data = parse_response(status_response)
        assert status_data["reviewer"] == "rachel-review"

    async def test_claim_pr_review_already_claimed(self, fresh_pr, setup_orchestrator):
        """claim_pr_review fails if already claimed."""
        pr_number = fresh_pr["number"]

        # First claim
        await orchestrator.impl_claim_pr_review(
            {"pr_number": pr_number, "reviewer_name": "First Reviewer"}
        )

        # Second claim should fail
        response = await orchestrator.impl_claim_pr_review(
            {"pr_number": pr_number, "reviewer_name": "Second Reviewer"}
        )
        expect_error(response, "already assigned")

    async def test_request_changes_adds_label(
        self, client, fresh_pr, setup_orchestrator
    ):
        """request_changes adds feedback-requested label and posts comment."""
        pr_number = fresh_pr["number"]

        # Use the orchestrator tool (uses COMMENT event, works on self-reviews)
        response = await orchestrator.impl_request_changes(
            {
                "pr_number": pr_number,
                "comment": "Needs some fixes",
                "agent_name": "Test Reviewer",
                "agent_type": "reviewer",
            }
        )
        text = parse_response(response)
        assert "Requested changes" in text

        # Verify has_feedback flag is detected
        status_response = await orchestrator.impl_get_pr_status(
            {"pr_number": pr_number}
        )
        status_data = parse_response(status_response)
        assert status_data["has_feedback"] is True

    async def test_request_changes_creates_comment_review(
        self, client, fresh_pr, setup_orchestrator
    ):
        """request_changes creates a COMMENT review (not REQUEST_CHANGES).

        Since all agents share one GitHub account, we use COMMENT events
        which work on self-reviews. Labels are the source of truth.
        """
        pr_number = fresh_pr["number"]

        response = await orchestrator.impl_request_changes(
            {
                "pr_number": pr_number,
                "comment": "Please fix the formatting",
                "agent_name": "Rachel Review",
                "agent_type": "reviewer",
            }
        )
        text = parse_response(response)
        assert "Requested changes" in text

        # Verify feedback label was added
        status_response = await orchestrator.impl_get_pr_status(
            {"pr_number": pr_number}
        )
        status_data = parse_response(status_response)
        assert status_data["has_feedback"] is True

    async def test_mark_feedback_addressed_removes_label(
        self, fresh_pr, setup_orchestrator
    ):
        """mark_feedback_addressed removes feedback-requested label."""
        pr_number = fresh_pr["number"]

        # First request changes
        await orchestrator.impl_request_changes(
            {
                "pr_number": pr_number,
                "comment": "Fix this",
                "agent_name": "Rachel Review",
                "agent_type": "reviewer",
            }
        )

        # Verify has_feedback is True
        status1 = parse_response(
            await orchestrator.impl_get_pr_status({"pr_number": pr_number})
        )
        assert status1["has_feedback"] is True

        # Mark addressed
        response = await orchestrator.impl_mark_feedback_addressed(
            {
                "pr_number": pr_number,
                "agent_name": "Bob Smith",
                "agent_type": "programmer",
                "comment": "Fixed the formatting issue",
            }
        )
        text = parse_response(response)
        assert "Marked feedback addressed" in text

        # Verify has_feedback is False
        status2 = parse_response(
            await orchestrator.impl_get_pr_status({"pr_number": pr_number})
        )
        assert status2["has_feedback"] is False

    async def test_approve_pr_adds_label_and_comment(
        self, client, fresh_pr, setup_orchestrator
    ):
        """approve_pr adds approved label and posts comment.

        Uses COMMENT event (not APPROVE) since all agents share one
        GitHub account. The status:approved label is the source of truth.
        """
        pr_number = fresh_pr["number"]

        response = await orchestrator.impl_approve_pr(
            {
                "pr_number": pr_number,
                "comment": "Looks good to me!",
                "agent_name": "Rachel Review",
                "agent_type": "reviewer",
            }
        )
        text = parse_response(response)
        assert "Approved" in text

        # Verify approved label was added
        status_response = await orchestrator.impl_get_pr_status(
            {"pr_number": pr_number}
        )
        status_data = parse_response(status_response)
        assert status_data["is_approved"] is True

    async def test_list_my_prs(self, client, fresh_pr, setup_orchestrator):
        """list_my_prs returns PRs assigned to author via assignee:* label."""
        # Note: This test is limited because the fresh_pr fixture may not have
        # the assignee label. We test that the tool runs without error.
        response = await orchestrator.impl_list_my_prs({"author_name": "Test Author"})
        data = parse_response(response)

        # Should return a list (may be empty)
        assert isinstance(data, list)

    async def test_list_my_reviews(self, client, fresh_pr, setup_orchestrator):
        """list_my_reviews returns PRs assigned to reviewer via reviewer:* label."""
        # Note: This test is limited because the fresh_pr fixture may not have
        # the reviewer label. We test that the tool runs without error.
        response = await orchestrator.impl_list_my_reviews(
            {"reviewer_name": "Test Reviewer"}
        )
        data = parse_response(response)

        # Should return a list (may be empty)
        assert isinstance(data, list)


# =============================================================================
# TestActivityTools
# =============================================================================


@pytest.mark.slow
class TestActivityTools:
    """Tests for activity logging tools."""

    async def test_log_activity_basic(self, fresh_epic, setup_orchestrator):
        """log_activity posts a comment on the epic."""
        response = await orchestrator.impl_log_activity(
            {
                "epic_number": fresh_epic["number"],
                "agent_name": "Test Agent",
                "agent_type": "programmer",
                "event_type": "started",
                "message": "Starting work on the project",
                "issue_number": None,
                "pr_number": None,
            }
        )
        data = parse_response(response)

        assert "comment_id" in data
        assert "Activity logged successfully" in data["message"]

    async def test_log_activity_with_issue_reference(
        self, fresh_epic, setup_orchestrator
    ):
        """log_activity includes issue reference."""
        epic_number = fresh_epic["number"]

        # Create a sub-issue
        result = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "Issue for activity",
                "body": "Test",
                "priority": "medium",
            }
        )
        sub_number = parse_response(result)["number"]
        fresh_epic["sub_issues"].append(sub_number)

        response = await orchestrator.impl_log_activity(
            {
                "epic_number": epic_number,
                "agent_name": "Test Agent",
                "agent_type": "programmer",
                "event_type": "claimed",
                "message": "Claimed this issue",
                "issue_number": sub_number,
                "pr_number": None,
            }
        )
        data = parse_response(response)
        assert "comment_id" in data

    async def test_log_activity_with_pr_reference(self, fresh_pr, setup_orchestrator):
        """log_activity includes PR reference."""
        response = await orchestrator.impl_log_activity(
            {
                "epic_number": fresh_pr["epic_number"],
                "agent_name": "Test Reviewer",
                "agent_type": "reviewer",
                "event_type": "review_started",
                "message": "Starting review",
                "issue_number": None,
                "pr_number": fresh_pr["number"],
            }
        )
        data = parse_response(response)
        assert "comment_id" in data

    async def test_get_activity_log_returns_entries(
        self, fresh_epic, setup_orchestrator
    ):
        """get_activity_log returns parsed activity entries."""
        epic_number = fresh_epic["number"]

        # Log some activities
        for i in range(3):
            await orchestrator.impl_log_activity(
                {
                    "epic_number": epic_number,
                    "agent_name": f"Agent {i + 1}",
                    "agent_type": "programmer",
                    "event_type": "progress",
                    "message": f"Progress update {i + 1}",
                    "issue_number": None,
                    "pr_number": None,
                }
            )

        response = await orchestrator.impl_get_activity_log(
            {"epic_number": epic_number, "limit": 10}
        )
        data = parse_response(response)

        assert len(data) == 3
        # Activities are returned newest first (reversed from GitHub's oldest-first order)
        agent_names = [d["agent_name"] for d in data]
        assert "Agent 1" in agent_names
        assert "Agent 2" in agent_names
        assert "Agent 3" in agent_names

    async def test_get_activity_log_ignores_non_activity_comments(
        self, client, fresh_epic, setup_orchestrator
    ):
        """get_activity_log ignores regular comments."""
        epic_number = fresh_epic["number"]

        # Post a regular comment
        await client.create_issue_comment(
            epic_number, "This is a regular comment, not an activity log."
        )

        # Post an activity
        await orchestrator.impl_log_activity(
            {
                "epic_number": epic_number,
                "agent_name": "Test Agent",
                "agent_type": "manager",
                "event_type": "started",
                "message": "Started managing",
                "issue_number": None,
                "pr_number": None,
            }
        )

        response = await orchestrator.impl_get_activity_log(
            {"epic_number": epic_number, "limit": 10}
        )
        data = parse_response(response)

        # Should only have the activity entry, not the regular comment
        assert len(data) == 1
        assert data[0]["agent_name"] == "Test Agent"

    async def test_get_activity_log_respects_limit(
        self, fresh_epic, setup_orchestrator
    ):
        """get_activity_log respects the limit parameter."""
        epic_number = fresh_epic["number"]

        # Log 5 activities
        for i in range(5):
            await orchestrator.impl_log_activity(
                {
                    "epic_number": epic_number,
                    "agent_name": f"Agent {i + 1}",
                    "agent_type": "programmer",
                    "event_type": "progress",
                    "message": f"Update {i + 1}",
                    "issue_number": None,
                    "pr_number": None,
                }
            )

        # Request with limit=3 (note: this limits GitHub API fetch, not parsed results)
        response = await orchestrator.impl_get_activity_log(
            {"epic_number": epic_number, "limit": 3}
        )
        data = parse_response(response)

        # Should have at most 3 entries
        assert len(data) <= 3


# =============================================================================
# TestSubIssueLifecycle (Workflow)
# =============================================================================


@pytest.mark.slow
class TestSubIssueLifecycle:
    """Tests for complete sub-issue workflow."""

    async def test_complete_sub_issue_workflow(self, fresh_epic, setup_orchestrator):
        """Test full sub-issue lifecycle from creation to completion."""
        epic_number = fresh_epic["number"]

        # Step 1: Create sub-issue (status:pending)
        result = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "Implement feature X",
                "body": "Full implementation of feature X",
                "priority": "high",
            }
        )
        sub_data = parse_response(result)
        sub_number = sub_data["number"]
        fresh_epic["sub_issues"].append(sub_number)

        # Step 2: Verify appears in available list
        available = parse_response(
            await orchestrator.impl_list_available_sub_issues(
                {"epic_number": epic_number, "priority": None}
            )
        )
        assert any(si["number"] == sub_number for si in available)

        # Step 3: Claim sub-issue (adds assignee, status:in-progress)
        await orchestrator.impl_claim_sub_issue(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_number,
                "agent_name": "Alice Chen",
            }
        )

        # Step 4: Verify NOT in available list
        available = parse_response(
            await orchestrator.impl_list_available_sub_issues(
                {"epic_number": epic_number, "priority": None}
            )
        )
        assert not any(si["number"] == sub_number for si in available)

        # Step 5: Verify in my_sub_issues
        my_issues = parse_response(
            await orchestrator.impl_list_my_sub_issues(
                {"epic_number": epic_number, "agent_name": "Alice Chen"}
            )
        )
        assert any(si["number"] == sub_number for si in my_issues)

        # Step 6: Verify status is in-progress
        sub_status = parse_response(
            await orchestrator.impl_get_sub_issue(
                {"epic_number": epic_number, "sub_issue_number": sub_number}
            )
        )
        assert sub_status["sf_status"] == "in-progress"
        assert sub_status["assignee"] == "alice-chen"

        # Step 7: Update status to in-review (simulating PR creation)
        await orchestrator.impl_update_sub_issue_status(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_number,
                "new_status": "in-review",
            }
        )

        # Step 8: Verify status changed
        sub_status = parse_response(
            await orchestrator.impl_get_sub_issue(
                {"epic_number": epic_number, "sub_issue_number": sub_number}
            )
        )
        assert sub_status["sf_status"] == "in-review"


# =============================================================================
# TestPRReviewLifecycle (Workflow)
# =============================================================================


@pytest.mark.slow
class TestPRReviewLifecycle:
    """Tests for complete PR review workflow."""

    async def test_complete_pr_review_workflow(
        self, client, fresh_pr, setup_orchestrator
    ):
        """Test full PR review lifecycle from creation to approval."""
        pr_number = fresh_pr["number"]
        epic_number = fresh_pr["epic_number"]

        # Step 1: Get initial PR status (no reviewer)
        status = parse_response(
            await orchestrator.impl_get_pr_status({"pr_number": pr_number})
        )
        assert status["reviewer"] is None
        assert status["has_feedback"] is False

        # Step 2: Verify appears in prs_for_review
        available = parse_response(
            await orchestrator.impl_list_prs_for_review({"epic_number": epic_number})
        )
        assert any(pr["number"] == pr_number for pr in available)

        # Step 3: Claim PR review (adds reviewer label)
        await orchestrator.impl_claim_pr_review(
            {"pr_number": pr_number, "reviewer_name": "Rachel Review"}
        )

        # Step 4: Verify NOT in prs_for_review
        available = parse_response(
            await orchestrator.impl_list_prs_for_review({"epic_number": epic_number})
        )
        assert not any(pr["number"] == pr_number for pr in available)

        # Step 5: Request changes via orchestrator (uses COMMENT event, works on self-reviews)
        await orchestrator.impl_request_changes(
            {
                "pr_number": pr_number,
                "comment": "Please fix the formatting",
                "agent_name": "Rachel Review",
                "agent_type": "reviewer",
            }
        )

        # Step 6: Verify has_feedback=true
        status = parse_response(
            await orchestrator.impl_get_pr_status({"pr_number": pr_number})
        )
        assert status["has_feedback"] is True

        # Step 7: Mark feedback addressed
        await orchestrator.impl_mark_feedback_addressed(
            {
                "pr_number": pr_number,
                "agent_name": "Bob Smith",
                "agent_type": "programmer",
                "comment": "Fixed the formatting",
            }
        )

        # Step 8: Verify has_feedback=false
        status = parse_response(
            await orchestrator.impl_get_pr_status({"pr_number": pr_number})
        )
        assert status["has_feedback"] is False

        # Step 9: Approve PR (uses COMMENT event + label, works on self-reviews)
        await orchestrator.impl_approve_pr(
            {
                "pr_number": pr_number,
                "comment": "Looks good now!",
                "agent_name": "Rachel Review",
                "agent_type": "reviewer",
            }
        )

        # Step 10: Verify is_approved=true
        status = parse_response(
            await orchestrator.impl_get_pr_status({"pr_number": pr_number})
        )
        assert status["is_approved"] is True


# =============================================================================
# TestRaceConditions
# =============================================================================


@pytest.mark.slow
class TestRaceConditions:
    """Tests for race condition handling."""

    async def test_concurrent_claim_sub_issue(self, fresh_epic, setup_orchestrator):
        """First agent claims, second agent gets error."""
        epic_number = fresh_epic["number"]

        # Create sub-issue
        result = await orchestrator.impl_create_sub_issue(
            {
                "epic_number": epic_number,
                "title": "Contested issue",
                "body": "Multiple agents want this",
                "priority": "high",
            }
        )
        sub_number = parse_response(result)["number"]
        fresh_epic["sub_issues"].append(sub_number)

        # First agent claims - should succeed
        response1 = await orchestrator.impl_claim_sub_issue(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_number,
                "agent_name": "First Agent",
            }
        )
        assert not response1.get("isError")

        # Second agent claims - should fail
        response2 = await orchestrator.impl_claim_sub_issue(
            {
                "epic_number": epic_number,
                "sub_issue_number": sub_number,
                "agent_name": "Second Agent",
            }
        )
        expect_error(response2, "already assigned")

    async def test_concurrent_claim_pr_review(self, fresh_pr, setup_orchestrator):
        """First reviewer claims, second reviewer gets error."""
        pr_number = fresh_pr["number"]

        # First reviewer claims - should succeed
        response1 = await orchestrator.impl_claim_pr_review(
            {"pr_number": pr_number, "reviewer_name": "First Reviewer"}
        )
        assert not response1.get("isError")

        # Second reviewer claims - should fail
        response2 = await orchestrator.impl_claim_pr_review(
            {"pr_number": pr_number, "reviewer_name": "Second Reviewer"}
        )
        expect_error(response2, "already assigned")


# =============================================================================
# TestEndToEndWorkflow
# =============================================================================


@pytest.mark.slow
class TestEndToEndWorkflow:
    """Complete end-to-end workflow test."""

    async def test_full_epic_workflow(self, client, setup_orchestrator):
        """Test complete epic workflow from creation to completion."""
        timestamp = int(time.time())

        # Initialize variables for cleanup
        pr_number = None
        branch_name = None

        # ===== MANAGER: Setup Phase =====

        # Create epic
        epic = await client.create_issue(
            title=f"[Test Epic] End-to-end workflow {timestamp}",
            body="Complete workflow test epic",
            labels=["type:epic"],
        )
        epic_number = epic["number"]

        try:
            # Create high-priority sub-issue
            high_result = await orchestrator.impl_create_sub_issue(
                {
                    "epic_number": epic_number,
                    "title": "High priority task",
                    "body": "Urgent work",
                    "priority": "high",
                }
            )
            high_sub = parse_response(high_result)["number"]

            # Create medium-priority sub-issue
            med_result = await orchestrator.impl_create_sub_issue(
                {
                    "epic_number": epic_number,
                    "title": "Medium priority task",
                    "body": "Regular work",
                    "priority": "medium",
                }
            )
            med_sub = parse_response(med_result)["number"]

            # Log manager activity
            await orchestrator.impl_log_activity(
                {
                    "epic_number": epic_number,
                    "agent_name": "Manager Bot",
                    "agent_type": "manager",
                    "event_type": "started",
                    "message": "Created epic and sub-issues",
                    "issue_number": None,
                    "pr_number": None,
                }
            )

            # ===== PROGRAMMER A: Takes high-priority task =====

            # List available (sees 2)
            available = parse_response(
                await orchestrator.impl_list_available_sub_issues(
                    {"epic_number": epic_number, "priority": None}
                )
            )
            assert len(available) == 2

            # Claim high-priority
            await orchestrator.impl_claim_sub_issue(
                {
                    "epic_number": epic_number,
                    "sub_issue_number": high_sub,
                    "agent_name": "Alice Chen",
                }
            )

            # Log claim
            await orchestrator.impl_log_activity(
                {
                    "epic_number": epic_number,
                    "agent_name": "Alice Chen",
                    "agent_type": "programmer",
                    "event_type": "claimed",
                    "message": "Claimed high-priority task",
                    "issue_number": high_sub,
                    "pr_number": None,
                }
            )

            # ===== PROGRAMMER B: Takes medium-priority task =====

            # List available (sees 1)
            available = parse_response(
                await orchestrator.impl_list_available_sub_issues(
                    {"epic_number": epic_number, "priority": None}
                )
            )
            assert len(available) == 1

            # Claim medium-priority
            await orchestrator.impl_claim_sub_issue(
                {
                    "epic_number": epic_number,
                    "sub_issue_number": med_sub,
                    "agent_name": "Bob Smith",
                }
            )

            # Update to in-progress
            await orchestrator.impl_update_sub_issue_status(
                {
                    "epic_number": epic_number,
                    "sub_issue_number": med_sub,
                    "new_status": "in-progress",
                }
            )

            # Simulate creating PR (create branch and PR)
            branch_name = f"test-e2e-{timestamp}"
            repo_info = await client._rest_get(f"/repos/{INTEGRATION_TEST_REPO}")
            default_branch = repo_info["default_branch"]
            ref = await client._rest_get(
                f"/repos/{INTEGRATION_TEST_REPO}/git/ref/heads/{default_branch}"
            )
            await client._rest_post(
                f"/repos/{INTEGRATION_TEST_REPO}/git/refs",
                {"ref": f"refs/heads/{branch_name}", "sha": ref["object"]["sha"]},
            )

            import base64

            await client._rest_put(
                f"/repos/{INTEGRATION_TEST_REPO}/contents/e2e-test-{timestamp}.md",
                {
                    "message": "Add e2e test file",
                    "content": base64.b64encode(b"# E2E Test\n").decode(),
                    "branch": branch_name,
                },
            )

            pr = await client._rest_post(
                f"/repos/{INTEGRATION_TEST_REPO}/pulls",
                {
                    "title": f"[E2E Test] PR for medium task {timestamp}",
                    "body": f"Closes #{med_sub}",
                    "head": branch_name,
                    "base": default_branch,
                },
            )
            pr_number = pr["number"]

            # Update to in-review
            await orchestrator.impl_update_sub_issue_status(
                {
                    "epic_number": epic_number,
                    "sub_issue_number": med_sub,
                    "new_status": "in-review",
                }
            )

            # Log PR creation
            await orchestrator.impl_log_activity(
                {
                    "epic_number": epic_number,
                    "agent_name": "Bob Smith",
                    "agent_type": "programmer",
                    "event_type": "pr_created",
                    "message": "Created PR for medium task",
                    "issue_number": med_sub,
                    "pr_number": pr_number,
                }
            )

            # ===== REVIEWER: Reviews PR =====

            # List PRs for review
            prs_for_review = parse_response(
                await orchestrator.impl_list_prs_for_review(
                    {"epic_number": epic_number}
                )
            )
            assert any(p["number"] == pr_number for p in prs_for_review)

            # Claim review
            await orchestrator.impl_claim_pr_review(
                {"pr_number": pr_number, "reviewer_name": "Rachel Review"}
            )

            # Request changes
            await orchestrator.impl_request_changes(
                {
                    "pr_number": pr_number,
                    "comment": "Please add documentation",
                    "agent_name": "Rachel Review",
                    "agent_type": "reviewer",
                }
            )

            await orchestrator.impl_log_activity(
                {
                    "epic_number": epic_number,
                    "agent_name": "Rachel Review",
                    "agent_type": "reviewer",
                    "event_type": "review_submitted",
                    "message": "Requested changes",
                    "issue_number": None,
                    "pr_number": pr_number,
                }
            )

            # ===== PROGRAMMER B: Addresses feedback =====

            # Check has_feedback
            pr_status = parse_response(
                await orchestrator.impl_get_pr_status({"pr_number": pr_number})
            )
            assert pr_status["has_feedback"] is True

            # Address feedback and mark addressed
            await orchestrator.impl_mark_feedback_addressed(
                {
                    "pr_number": pr_number,
                    "agent_name": "Bob Smith",
                    "agent_type": "programmer",
                    "comment": "Added documentation as requested",
                }
            )

            await orchestrator.impl_log_activity(
                {
                    "epic_number": epic_number,
                    "agent_name": "Bob Smith",
                    "agent_type": "programmer",
                    "event_type": "feedback_addressed",
                    "message": "Added documentation",
                    "issue_number": None,
                    "pr_number": pr_number,
                }
            )

            # ===== REVIEWER: Checks feedback status =====

            # Check has_feedback is now false
            pr_status = parse_response(
                await orchestrator.impl_get_pr_status({"pr_number": pr_number})
            )
            assert pr_status["has_feedback"] is False

            # Note: Skipping actual approval because GitHub doesn't allow
            # approving your own PR. Log the activity as if approved.
            await orchestrator.impl_log_activity(
                {
                    "epic_number": epic_number,
                    "agent_name": "Rachel Review",
                    "agent_type": "reviewer",
                    "event_type": "review_submitted",
                    "message": "Reviewed PR (approval skipped in test)",
                    "issue_number": None,
                    "pr_number": pr_number,
                }
            )

            # ===== MANAGER: Check activity log =====

            activity = parse_response(
                await orchestrator.impl_get_activity_log(
                    {"epic_number": epic_number, "limit": 20}
                )
            )
            assert len(activity) >= 6  # At least 6 activity entries

            # Verify different agent types logged
            agent_types = {a["agent_type"] for a in activity}
            assert "manager" in agent_types
            assert "programmer" in agent_types
            assert "reviewer" in agent_types

            # ===== MANAGER: Close epic =====
            await orchestrator.impl_close_epic({"epic_number": epic_number})

            # Verify epic is closed
            epic_data = await client.get_issue(epic_number)
            assert epic_data["state"] == "closed"

        finally:
            # Cleanup
            try:
                # Close sub-issues
                sub_issues = await client.get_sub_issues(epic_number)
                for si in sub_issues:
                    try:
                        await client.close_issue(si["number"])
                    except Exception:
                        pass

                # Close epic
                try:
                    await client.close_issue(epic_number)
                except Exception:
                    pass

                # Close PR and delete branch (if they were created)
                if pr_number is not None:
                    try:
                        await client._rest_patch(
                            f"/repos/{INTEGRATION_TEST_REPO}/pulls/{pr_number}",
                            {"state": "closed"},
                        )
                    except Exception:
                        pass
                if branch_name is not None:
                    try:
                        await client._rest_delete(
                            f"/repos/{INTEGRATION_TEST_REPO}/git/refs/heads/{branch_name}"
                        )
                    except Exception:
                        pass
            except Exception as e:
                print(f"E2E cleanup warning: {e}")
