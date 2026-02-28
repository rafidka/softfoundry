"""Tests for the GitHub API client.

This module tests the GitHubClient class which provides async HTTP access
to GitHub's REST and GraphQL APIs.

Test organization:
- TestIntegration: Real API tests (marked slow, skipped by default)
"""

import pytest
import pytest_asyncio

from softfoundry.mcp.github_client import GitHubClient, GitHubClientError
from softfoundry.mcp.types import EpicStatus, PRStatus, SubIssueStatus

# Integration test repository
INTEGRATION_TEST_REPO = "rafidka/softfoundry-integ-tests"


# -----------------------------------------------------------------------------
# TestIntegration
# -----------------------------------------------------------------------------


@pytest.mark.slow
class TestIntegration:
    """Integration tests using real GitHub API.

    These tests are marked as slow and skipped by default.
    Run with: pytest -m slow

    Prerequisites:
    - gh CLI installed and authenticated
    - Access to the integration test repo

    Test organization:
    - TestIntegrationAuth: Authentication tests
    - TestIntegrationREST: Low-level REST API tests
    - TestIntegrationGraphQL: Low-level GraphQL API tests
    - TestIntegrationLabels: Label management tests
    - TestIntegrationIssues: Issue CRUD tests
    - TestIntegrationComments: Comment tests
    - TestIntegrationSubIssues: Sub-issue (epic) tests
    - TestIntegrationPRs: Pull request tests
    - TestIntegrationHighLevel: High-level method tests
    """

    @pytest_asyncio.fixture
    async def real_client(self):
        """Create a real GitHubClient for integration tests."""
        owner, repo = INTEGRATION_TEST_REPO.split("/")
        client = GitHubClient(owner, repo)
        yield client
        await client.close()

    # -------------------------------------------------------------------------
    # Authentication Tests
    # -------------------------------------------------------------------------

    async def test_auth_token_retrieval(self, real_client):
        """Can authenticate with real gh CLI."""
        token = real_client.token
        assert token is not None
        assert len(token) > 0
        # GitHub tokens start with specific prefixes
        assert token.startswith(("ghp_", "gho_", "ghu_", "ghs_", "ghr_"))

    async def test_auth_token_cached(self, real_client):
        """Token is cached after first retrieval."""
        token1 = real_client.token
        token2 = real_client.token
        assert token1 == token2

    # -------------------------------------------------------------------------
    # Low-Level REST API Tests
    # -------------------------------------------------------------------------

    async def test_rest_get_repo(self, real_client):
        """Can GET repository info."""
        result = await real_client._rest_get(f"/repos/{INTEGRATION_TEST_REPO}")

        assert result["full_name"] == INTEGRATION_TEST_REPO
        assert "id" in result

    async def test_rest_get_with_params(self, real_client):
        """Can GET with query parameters."""
        result = await real_client._rest_get(
            f"/repos/{INTEGRATION_TEST_REPO}/issues",
            params={"state": "all", "per_page": 1},
        )

        assert isinstance(result, list)
        # per_page=1 should return at most 1 issue
        assert len(result) <= 1

    async def test_rest_get_404_error(self, real_client):
        """GET returns proper error for non-existent resource."""
        with pytest.raises(GitHubClientError) as exc_info:
            await real_client._rest_get(
                f"/repos/{INTEGRATION_TEST_REPO}/issues/999999999"
            )

        assert "404" in str(exc_info.value)

    # -------------------------------------------------------------------------
    # Low-Level GraphQL API Tests
    # -------------------------------------------------------------------------

    async def test_graphql_repository_query(self, real_client):
        """Can execute GraphQL repository query."""
        owner, repo = INTEGRATION_TEST_REPO.split("/")
        query = """
        query GetRepo($owner: String!, $repo: String!) {
            repository(owner: $owner, name: $repo) {
                name
                owner { login }
                description
                isPrivate
            }
        }
        """

        result = await real_client._graphql(query, {"owner": owner, "repo": repo})

        assert result["repository"]["name"] == repo
        assert result["repository"]["owner"]["login"] == owner
        assert isinstance(result["repository"]["isPrivate"], bool)

    async def test_graphql_viewer_query(self, real_client):
        """Can query authenticated user info."""
        query = """
        query {
            viewer {
                login
            }
        }
        """

        result = await real_client._graphql(query, {})

        assert "viewer" in result
        assert "login" in result["viewer"]
        assert len(result["viewer"]["login"]) > 0

    async def test_graphql_error_handling(self, real_client):
        """GraphQL returns proper error for invalid query."""
        with pytest.raises(GitHubClientError) as exc_info:
            await real_client._graphql("query { invalidField }", {})

        assert "GraphQL errors" in str(exc_info.value)

    # -------------------------------------------------------------------------
    # Label Tests
    # -------------------------------------------------------------------------

    async def test_create_label(self, real_client):
        """Can create a new label."""
        import time

        label_name = f"test-label-{int(time.time())}"

        result = await real_client.create_label(
            name=label_name,
            color="ff0000",
            description="Test label created by integration tests",
        )

        assert result["name"] == label_name
        assert result["color"] == "ff0000"

    async def test_create_label_update_existing(self, real_client):
        """Can update an existing label."""
        import time

        label_name = f"test-update-{int(time.time())}"

        # Create first
        await real_client.create_label(
            name=label_name, color="00ff00", description="Original"
        )

        # Update (same name, different color)
        result = await real_client.create_label(
            name=label_name, color="0000ff", description="Updated"
        )

        assert result["name"] == label_name
        assert result["color"] == "0000ff"

    # -------------------------------------------------------------------------
    # Issue CRUD Tests
    # -------------------------------------------------------------------------

    async def test_create_issue(self, real_client):
        """Can create a new issue."""
        import time

        timestamp = int(time.time())
        title = f"[Test] Integration test issue {timestamp}"

        result = await real_client.create_issue(
            title=title,
            body="This issue was created by an integration test.\n\nIt can be safely closed.",
            labels=["test"],
        )

        assert result["title"] == title
        assert result["state"] == "open"
        assert "number" in result

    async def test_get_issue(self, real_client):
        """Can retrieve an existing issue."""
        import time

        # Create an issue first
        timestamp = int(time.time())
        created = await real_client.create_issue(
            title=f"[Test] Get issue test {timestamp}",
            body="Test issue for get_issue test",
        )
        issue_number = created["number"]

        # Now get it
        result = await real_client.get_issue(issue_number)

        assert result["number"] == issue_number
        assert result["title"] == created["title"]
        assert result["state"] == "open"

    async def test_get_issue_node_id(self, real_client):
        """Can retrieve GraphQL node ID for an issue."""
        import time

        # Create an issue first
        timestamp = int(time.time())
        created = await real_client.create_issue(
            title=f"[Test] Node ID test {timestamp}",
            body="Test issue for get_issue_node_id test",
        )
        issue_number = created["number"]

        # Get node ID
        node_id = await real_client.get_issue_node_id(issue_number)

        # GitHub node IDs have a specific format
        assert node_id is not None
        assert len(node_id) > 0
        assert node_id.startswith("I_")  # Issue node IDs start with I_

    async def test_update_issue_labels(self, real_client):
        """Can add and remove labels from an issue."""
        import time

        # Create test labels
        timestamp = int(time.time())
        label1 = f"label-a-{timestamp}"
        label2 = f"label-b-{timestamp}"
        await real_client.create_label(label1, "aaaaaa", "Test label A")
        await real_client.create_label(label2, "bbbbbb", "Test label B")

        # Create issue with label1
        created = await real_client.create_issue(
            title=f"[Test] Label update test {timestamp}",
            body="Test issue for update_issue_labels test",
            labels=[label1],
        )
        issue_number = created["number"]

        # Update: remove label1, add label2
        result = await real_client.update_issue_labels(
            issue_number,
            add_labels=[label2],
            remove_labels=[label1],
        )

        label_names = [label["name"] for label in result["labels"]]
        assert label2 in label_names
        assert label1 not in label_names

    async def test_close_issue(self, real_client):
        """Can close an issue."""
        import time

        # Create an issue first
        timestamp = int(time.time())
        created = await real_client.create_issue(
            title=f"[Test] Close issue test {timestamp}",
            body="Test issue for close_issue test - will be closed immediately",
        )
        issue_number = created["number"]

        # Close it
        result = await real_client.close_issue(issue_number)

        assert result["state"] == "closed"

    # -------------------------------------------------------------------------
    # Comment Tests
    # -------------------------------------------------------------------------

    async def test_create_issue_comment(self, real_client):
        """Can create a comment on an issue."""
        import time

        # Create an issue first
        timestamp = int(time.time())
        created = await real_client.create_issue(
            title=f"[Test] Comment test {timestamp}",
            body="Test issue for comment tests",
        )
        issue_number = created["number"]

        # Create a comment
        comment_body = f"Test comment created at {timestamp}"
        result = await real_client.create_issue_comment(issue_number, comment_body)

        assert result["body"] == comment_body
        assert "id" in result

    async def test_list_issue_comments(self, real_client):
        """Can list comments on an issue."""
        import time

        # Create an issue first
        timestamp = int(time.time())
        created = await real_client.create_issue(
            title=f"[Test] List comments test {timestamp}",
            body="Test issue for list_issue_comments test",
        )
        issue_number = created["number"]

        # Create multiple comments
        await real_client.create_issue_comment(issue_number, "Comment 1")
        await real_client.create_issue_comment(issue_number, "Comment 2")
        await real_client.create_issue_comment(issue_number, "Comment 3")

        # List comments
        result = await real_client.list_issue_comments(issue_number, per_page=10)

        assert isinstance(result, list)
        assert len(result) >= 3
        bodies = [c["body"] for c in result]
        assert "Comment 1" in bodies
        assert "Comment 2" in bodies
        assert "Comment 3" in bodies

    # -------------------------------------------------------------------------
    # Sub-Issue (Epic) Tests
    # -------------------------------------------------------------------------

    async def test_get_sub_issues_empty(self, real_client):
        """Can query sub-issues for an issue with no sub-issues."""
        import time

        # Create an issue (not an epic, no sub-issues)
        timestamp = int(time.time())
        created = await real_client.create_issue(
            title=f"[Test] Empty sub-issues test {timestamp}",
            body="Test issue with no sub-issues",
        )
        issue_number = created["number"]

        # Get sub-issues (should be empty)
        result = await real_client.get_sub_issues(issue_number)

        assert isinstance(result, list)
        assert len(result) == 0

    async def test_add_sub_issue(self, real_client):
        """Can link a sub-issue to a parent issue."""
        import time

        timestamp = int(time.time())

        # Create parent (epic) issue
        parent = await real_client.create_issue(
            title=f"[Test] Epic for sub-issue test {timestamp}",
            body="Parent issue for add_sub_issue test",
            labels=["type:epic"],
        )
        parent_number = parent["number"]

        # Create sub-issue
        sub = await real_client.create_issue(
            title=f"[Test] Sub-issue {timestamp}",
            body="Sub-issue for add_sub_issue test",
        )
        sub_number = sub["number"]

        # Get node IDs
        parent_node_id = await real_client.get_issue_node_id(parent_number)
        sub_node_id = await real_client.get_issue_node_id(sub_number)

        # Link sub-issue to parent
        result = await real_client.add_sub_issue(parent_node_id, sub_node_id)

        assert result["issue"]["number"] == parent_number
        assert result["subIssue"]["number"] == sub_number

    async def test_get_sub_issues(self, real_client):
        """Can retrieve sub-issues of a parent issue."""
        import time

        timestamp = int(time.time())

        # Create parent (epic) issue
        parent = await real_client.create_issue(
            title=f"[Test] Epic for get_sub_issues test {timestamp}",
            body="Parent issue for get_sub_issues test",
            labels=["type:epic"],
        )
        parent_number = parent["number"]

        # Create and link multiple sub-issues
        for i in range(2):
            sub = await real_client.create_issue(
                title=f"[Test] Sub-issue {i + 1} of {timestamp}",
                body=f"Sub-issue {i + 1}",
            )
            parent_node_id = await real_client.get_issue_node_id(parent_number)
            sub_node_id = await real_client.get_issue_node_id(sub["number"])
            await real_client.add_sub_issue(parent_node_id, sub_node_id)

        # Get sub-issues
        result = await real_client.get_sub_issues(parent_number)

        assert isinstance(result, list)
        assert len(result) == 2
        titles = [si["title"] for si in result]
        assert any("Sub-issue 1" in t for t in titles)
        assert any("Sub-issue 2" in t for t in titles)

    # -------------------------------------------------------------------------
    # Pull Request Tests
    # -------------------------------------------------------------------------

    async def test_list_prs(self, real_client):
        """Can list pull requests."""
        result = await real_client.list_prs(state="all", per_page=5)

        assert isinstance(result, list)  # May be empty if no PRs exist

    async def test_list_prs_open(self, real_client):
        """Can list open pull requests."""
        result = await real_client.list_prs(state="open", per_page=10)

        assert isinstance(result, list)
        # All returned PRs should be open
        for pr in result:
            assert pr["state"] == "open"

    async def test_list_prs_closed(self, real_client):
        """Can list closed pull requests."""
        result = await real_client.list_prs(state="closed", per_page=10)

        assert isinstance(result, list)
        # All returned PRs should be closed
        for pr in result:
            assert pr["state"] == "closed"

    # Note: Testing get_pr, get_pr_reviews, create_pr_review requires an existing PR.
    # These tests use pre-created test data in the integration test repo.

    @pytest.fixture
    def test_pr_number(self):
        """PR number for PR-related tests.

        This should be a PR in the integration test repo that:
        - Exists and is accessible
        - Has at least one review
        - Can be used for read-only tests

        Update this value if the test PR changes.
        """
        return 43  # PR created for integration testing

    async def test_get_pr(self, real_client, test_pr_number):
        """Can retrieve a pull request."""
        # Skip if no test PR is configured
        if test_pr_number is None:
            pytest.skip("No test PR configured")

        try:
            result = await real_client.get_pr(test_pr_number)

            assert result["number"] == test_pr_number
            assert "title" in result
            assert "state" in result
            assert result["state"] in ("open", "closed")
            assert "user" in result
            assert "head" in result
            assert "base" in result
        except GitHubClientError as e:
            if "404" in str(e):
                pytest.skip(f"Test PR #{test_pr_number} not found in test repo")
            raise

    async def test_get_pr_reviews(self, real_client, test_pr_number):
        """Can retrieve reviews for a pull request."""
        if test_pr_number is None:
            pytest.skip("No test PR configured")

        try:
            result = await real_client.get_pr_reviews(test_pr_number)

            assert isinstance(result, list)
            # Each review should have expected fields
            for review in result:
                assert "id" in review
                assert "state" in review
                assert review["state"] in (
                    "PENDING",
                    "APPROVED",
                    "CHANGES_REQUESTED",
                    "COMMENTED",
                    "DISMISSED",
                )
        except GitHubClientError as e:
            if "404" in str(e):
                pytest.skip(f"Test PR #{test_pr_number} not found in test repo")
            raise

    # Note: create_pr_review is not tested here as it would modify the PR state.
    # It's covered by unit tests with mocked responses.

    # -------------------------------------------------------------------------
    # High-Level Method Tests
    # -------------------------------------------------------------------------

    async def test_get_epic_status(self, real_client):
        """Can get full epic status with sub-issues."""
        import time

        timestamp = int(time.time())

        # Create labels for the test
        await real_client.create_label("status:pending", "fbca04", "Pending status")
        await real_client.create_label("status:in-progress", "0e8a16", "In progress")
        await real_client.create_label("priority:high", "d73a4a", "High priority")
        await real_client.create_label("assignee:test-user", "0366d6", "Test assignee")

        # Create epic
        epic = await real_client.create_issue(
            title=f"[Test] Epic for get_epic_status {timestamp}",
            body="Epic for testing get_epic_status",
            labels=["type:epic"],
        )
        epic_number = epic["number"]

        # Create sub-issues with various labels
        sub1 = await real_client.create_issue(
            title=f"[Test] Sub-issue 1 of {timestamp}",
            body="First sub-issue",
            labels=["status:in-progress", "priority:high", "assignee:test-user"],
        )
        sub2 = await real_client.create_issue(
            title=f"[Test] Sub-issue 2 of {timestamp}",
            body="Second sub-issue",
            labels=["status:pending"],
        )

        # Link sub-issues
        epic_node_id = await real_client.get_issue_node_id(epic_number)
        sub1_node_id = await real_client.get_issue_node_id(sub1["number"])
        sub2_node_id = await real_client.get_issue_node_id(sub2["number"])
        await real_client.add_sub_issue(epic_node_id, sub1_node_id)
        await real_client.add_sub_issue(epic_node_id, sub2_node_id)

        # Get epic status
        result = await real_client.get_epic_status(epic_number)

        assert isinstance(result, EpicStatus)
        assert result.number == epic_number
        assert result.total_sub_issues == 2
        assert result.completed_sub_issues == 0  # None closed yet

        # Check sub-issues
        assert len(result.sub_issues) == 2
        sub_numbers = [s.number for s in result.sub_issues]
        assert sub1["number"] in sub_numbers
        assert sub2["number"] in sub_numbers

        # Find sub1 and check its parsed labels
        for sub in result.sub_issues:
            if sub.number == sub1["number"]:
                assert sub.status == "in-progress"
                assert sub.priority == "high"
                assert sub.assignee == "test-user"

    async def test_get_sub_issue_status(self, real_client):
        """Can get status of a specific sub-issue."""
        import time

        timestamp = int(time.time())

        # Create labels
        await real_client.create_label("status:pending", "fbca04", "Pending")

        # Create epic and sub-issue
        epic = await real_client.create_issue(
            title=f"[Test] Epic for get_sub_issue_status {timestamp}",
            body="Epic for testing",
        )
        sub = await real_client.create_issue(
            title=f"[Test] Sub-issue for status test {timestamp}",
            body="Sub-issue for testing",
            labels=["status:pending"],
        )

        # Link
        epic_node_id = await real_client.get_issue_node_id(epic["number"])
        sub_node_id = await real_client.get_issue_node_id(sub["number"])
        await real_client.add_sub_issue(epic_node_id, sub_node_id)

        # Get sub-issue status
        result = await real_client.get_sub_issue_status(epic["number"], sub["number"])

        assert isinstance(result, SubIssueStatus)
        assert result.number == sub["number"]
        assert result.status == "pending"
        assert result.state == "open"

    async def test_get_sub_issue_status_not_found(self, real_client):
        """get_sub_issue_status raises error for non-existent sub-issue."""
        import time

        timestamp = int(time.time())

        # Create epic with no sub-issues
        epic = await real_client.create_issue(
            title=f"[Test] Epic for not found test {timestamp}",
            body="Epic with no sub-issues",
        )

        with pytest.raises(GitHubClientError) as exc_info:
            await real_client.get_sub_issue_status(epic["number"], 999999)

        assert "not part of epic" in str(exc_info.value)

    async def test_get_pr_status(self, real_client, test_pr_number):
        """Can get full PR status."""
        if test_pr_number is None:
            pytest.skip("No test PR configured")

        try:
            result = await real_client.get_pr_status(test_pr_number)

            assert isinstance(result, PRStatus)
            assert result.number == test_pr_number
            assert result.state in ("open", "closed", "merged")
            assert result.author is not None
            assert result.head_branch is not None
            assert result.base_branch is not None
            assert isinstance(result.has_feedback, bool)
            assert isinstance(result.mergeable, bool)
            assert isinstance(result.has_conflicts, bool)
        except GitHubClientError as e:
            if "404" in str(e):
                pytest.skip(f"Test PR #{test_pr_number} not found in test repo")
            raise

    async def test_parse_activity_comment_from_real_data(self, real_client):
        """Can parse activity comments from real issue comments."""
        import time

        timestamp = int(time.time())

        # Create an issue
        issue = await real_client.create_issue(
            title=f"[Test] Activity parsing test {timestamp}",
            body="Issue for testing activity comment parsing",
        )

        # Create an activity-formatted comment
        activity_body = (
            f"**[2026-02-27 10:30 UTC] Test Agent** (Programmer)\n"
            f"**Event:** `started`\n"
            f"**Issue:** #{issue['number']}\n"
            f"**Message:** Starting work on this test issue"
        )
        comment = await real_client.create_issue_comment(issue["number"], activity_body)

        # Parse it
        result = await real_client.parse_activity_comment(comment)

        assert result is not None
        assert result.agent_name == "Test Agent"
        assert result.agent_type == "programmer"
        assert result.event_type == "started"
        assert result.issue_number == issue["number"]
        assert result.message == "Starting work on this test issue"

    async def test_parse_activity_comment_regular_comment(self, real_client):
        """parse_activity_comment returns None for regular comments."""
        import time

        timestamp = int(time.time())

        # Create an issue
        issue = await real_client.create_issue(
            title=f"[Test] Regular comment test {timestamp}",
            body="Issue for testing",
        )

        # Create a regular comment (not activity format)
        comment = await real_client.create_issue_comment(
            issue["number"], "This is just a regular comment, not an activity log."
        )

        # Parse it
        result = await real_client.parse_activity_comment(comment)

        assert result is None

    # -------------------------------------------------------------------------
    # Edge Cases and Error Handling
    # -------------------------------------------------------------------------

    async def test_rest_post_validation_error(self, real_client):
        """REST POST returns proper error for invalid data."""
        with pytest.raises(GitHubClientError) as exc_info:
            # Try to create an issue with empty title (should fail)
            await real_client._rest_post(
                f"/repos/{INTEGRATION_TEST_REPO}/issues",
                {"title": "", "body": "Invalid issue"},
            )

        # GitHub returns 422 for validation errors
        assert "422" in str(exc_info.value) or "400" in str(exc_info.value)

    async def test_graphql_not_found_error(self, real_client):
        """GraphQL returns error for non-existent resources."""
        owner, repo = INTEGRATION_TEST_REPO.split("/")
        query = """
        query GetIssue($owner: String!, $repo: String!, $number: Int!) {
            repository(owner: $owner, name: $repo) {
                issue(number: $number) {
                    id
                    title
                }
            }
        }
        """

        # Query for a non-existent issue number - GitHub returns a GraphQL error
        with pytest.raises(GitHubClientError) as exc_info:
            await real_client._graphql(
                query, {"owner": owner, "repo": repo, "number": 999999999}
            )

        assert "NOT_FOUND" in str(exc_info.value) or "Could not resolve" in str(
            exc_info.value
        )
