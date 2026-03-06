"""Tests for the GitHub API client.

This module tests the GitHubClient class which provides async HTTP access
to GitHub's REST and GraphQL APIs.

Test organization:
- TestTokenRetrieval: Tests for _get_gh_token() subprocess calls
- TestRESTMethods: Tests for _rest_get, _rest_post, _rest_patch
- TestGraphQLMethods: Tests for _graphql
- TestLabelParsing: Tests for label parsing (pure functions)
- TestIssueMethods: Tests for issue-related API methods
- TestPRMethods: Tests for PR-related API methods
- TestHighLevelMethods: Tests for get_epic_status, get_pr_status, etc.
- TestActivityParsing: Tests for parse_activity_comment
- TestIntegration: Real API tests (marked slow, skipped by default)
"""

import subprocess
from unittest.mock import Mock, patch

import httpx
import pytest
import respx

from softfoundry.mcp.constants import DEFAULT_GITHUB_REPO
from softfoundry.mcp.github_client import GitHubClient, GitHubClientError
from softfoundry.mcp.types import EpicStatus, PRStatus, SubIssueStatus

# Integration test repository
INTEGRATION_TEST_REPO = DEFAULT_GITHUB_REPO


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_token():
    """Mock gh auth token to avoid subprocess calls."""
    with patch("softfoundry.mcp.github_client.subprocess.run") as mock:
        mock.return_value = Mock(stdout="ghp_test_token_123\n", returncode=0)
        yield mock


@pytest.fixture
def client(mock_token):
    """Create a GitHubClient with mocked token.

    Note: We don't use async cleanup here because respx.mock handles
    the httpx client lifecycle during tests.
    """
    return GitHubClient("test-owner", "test-repo")


# -----------------------------------------------------------------------------
# TestTokenRetrieval
# -----------------------------------------------------------------------------


class TestTokenRetrieval:
    """Tests for _get_gh_token() subprocess calls."""

    def test_get_token_success(self):
        """Successfully gets token from gh auth token."""
        with patch("softfoundry.mcp.github_client.subprocess.run") as mock:
            mock.return_value = Mock(stdout="ghp_abc123\n", returncode=0)

            client = GitHubClient("owner", "repo")
            token = client.token

            assert token == "ghp_abc123"
            mock.assert_called_once_with(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                check=True,
            )

    def test_get_token_subprocess_error(self):
        """Raises GitHubClientError when gh command fails."""
        with patch("softfoundry.mcp.github_client.subprocess.run") as mock:
            mock.side_effect = subprocess.CalledProcessError(
                1, "gh", stderr="not logged in"
            )

            client = GitHubClient("owner", "repo")

            with pytest.raises(GitHubClientError) as exc_info:
                _ = client.token

            assert "Failed to get GitHub token" in str(exc_info.value)

    def test_get_token_gh_not_found(self):
        """Raises GitHubClientError when gh CLI not found."""
        with patch("softfoundry.mcp.github_client.subprocess.run") as mock:
            mock.side_effect = FileNotFoundError()

            client = GitHubClient("owner", "repo")

            with pytest.raises(GitHubClientError) as exc_info:
                _ = client.token

            assert "gh CLI not found" in str(exc_info.value)

    def test_token_cached(self):
        """Token is cached after first retrieval."""
        with patch("softfoundry.mcp.github_client.subprocess.run") as mock:
            mock.return_value = Mock(stdout="ghp_cached\n", returncode=0)

            client = GitHubClient("owner", "repo")

            # First access
            token1 = client.token
            # Second access
            token2 = client.token

            assert token1 == token2 == "ghp_cached"
            # Should only be called once due to caching
            assert mock.call_count == 1


# -----------------------------------------------------------------------------
# TestRESTMethods
# -----------------------------------------------------------------------------


class TestRESTMethods:
    """Tests for _rest_get, _rest_post, _rest_patch."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_rest_get_success(self, client):
        """GET request returns JSON on 200."""
        respx.get("https://api.github.com/repos/test-owner/test-repo/issues/1").mock(
            return_value=httpx.Response(200, json={"number": 1, "title": "Test Issue"})
        )

        result = await client._rest_get("/repos/test-owner/test-repo/issues/1")

        assert result == {"number": 1, "title": "Test Issue"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_rest_get_with_params(self, client):
        """GET request includes query parameters."""
        route = respx.get("https://api.github.com/test").mock(
            return_value=httpx.Response(200, json={"data": "value"})
        )

        result = await client._rest_get(
            "/test", params={"state": "open", "per_page": 10}
        )

        assert result == {"data": "value"}
        assert route.called
        # Check params were passed
        request = route.calls[0].request
        assert b"state=open" in request.url.query
        assert b"per_page=10" in request.url.query

    @pytest.mark.asyncio
    @respx.mock
    async def test_rest_get_404_error(self, client):
        """GET request raises error on 404."""
        respx.get("https://api.github.com/repos/test-owner/test-repo/issues/999").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        with pytest.raises(GitHubClientError) as exc_info:
            await client._rest_get("/repos/test-owner/test-repo/issues/999")

        assert "404" in str(exc_info.value)
        assert "Not Found" in str(exc_info.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_rest_get_500_error(self, client):
        """GET request raises error on 500."""
        respx.get("https://api.github.com/error").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        with pytest.raises(GitHubClientError) as exc_info:
            await client._rest_get("/error")

        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_rest_post_success_200(self, client):
        """POST request returns JSON on 200."""
        respx.post("https://api.github.com/test").mock(
            return_value=httpx.Response(200, json={"id": 123})
        )

        result = await client._rest_post("/test", {"data": "value"})

        assert result == {"id": 123}

    @pytest.mark.asyncio
    @respx.mock
    async def test_rest_post_success_201(self, client):
        """POST request returns JSON on 201 (created)."""
        respx.post("https://api.github.com/repos/test-owner/test-repo/issues").mock(
            return_value=httpx.Response(201, json={"number": 42, "title": "New Issue"})
        )

        result = await client._rest_post(
            "/repos/test-owner/test-repo/issues",
            {"title": "New Issue", "body": "Description"},
        )

        assert result == {"number": 42, "title": "New Issue"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_rest_post_400_error(self, client):
        """POST request raises error on 400."""
        respx.post("https://api.github.com/test").mock(
            return_value=httpx.Response(400, text="Bad Request")
        )

        with pytest.raises(GitHubClientError) as exc_info:
            await client._rest_post("/test", {"invalid": "data"})

        assert "400" in str(exc_info.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_rest_patch_success(self, client):
        """PATCH request returns JSON on 200."""
        respx.patch("https://api.github.com/repos/test-owner/test-repo/issues/1").mock(
            return_value=httpx.Response(200, json={"number": 1, "state": "closed"})
        )

        result = await client._rest_patch(
            "/repos/test-owner/test-repo/issues/1", {"state": "closed"}
        )

        assert result == {"number": 1, "state": "closed"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_rest_patch_error(self, client):
        """PATCH request raises error on 422."""
        respx.patch("https://api.github.com/test").mock(
            return_value=httpx.Response(422, text="Validation Failed")
        )

        with pytest.raises(GitHubClientError) as exc_info:
            await client._rest_patch("/test", {"invalid": "data"})

        assert "422" in str(exc_info.value)


# -----------------------------------------------------------------------------
# TestGraphQLMethods
# -----------------------------------------------------------------------------


class TestGraphQLMethods:
    """Tests for _graphql."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_graphql_success(self, client):
        """GraphQL query returns data on success."""
        respx.post("https://api.github.com/graphql").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"repository": {"issue": {"id": "I_123", "number": 1}}}},
            )
        )

        query = """
        query GetIssue($owner: String!, $repo: String!, $number: Int!) {
            repository(owner: $owner, name: $repo) {
                issue(number: $number) { id number }
            }
        }
        """

        result = await client._graphql(
            query, {"owner": "test-owner", "repo": "test-repo", "number": 1}
        )

        assert result == {"repository": {"issue": {"id": "I_123", "number": 1}}}

    @pytest.mark.asyncio
    @respx.mock
    async def test_graphql_errors(self, client):
        """GraphQL raises error when response contains errors."""
        respx.post("https://api.github.com/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [
                        {"message": "Field 'invalid' doesn't exist on type 'Query'"}
                    ]
                },
            )
        )

        with pytest.raises(GitHubClientError) as exc_info:
            await client._graphql("query { invalid }", {})

        assert "GraphQL errors" in str(exc_info.value)
        assert "doesn't exist" in str(exc_info.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_graphql_http_error(self, client):
        """GraphQL raises error on non-200 status."""
        respx.post("https://api.github.com/graphql").mock(
            return_value=httpx.Response(401, text="Bad credentials")
        )

        with pytest.raises(GitHubClientError) as exc_info:
            await client._graphql("query { viewer { login } }", {})

        assert "401" in str(exc_info.value)


# -----------------------------------------------------------------------------
# TestLabelParsing
# -----------------------------------------------------------------------------


class TestLabelParsing:
    """Tests for label parsing (pure functions)."""

    def test_parse_labels_status(self, mock_token):
        """Extracts status:* labels correctly."""
        client = GitHubClient("owner", "repo")
        labels = [{"name": "status:in-progress"}, {"name": "priority:high"}]

        status, assignee, priority, has_feedback, is_approved = client._parse_labels(
            labels
        )

        assert status == "in-progress"
        assert assignee is None
        assert priority == "high"
        assert has_feedback is False
        assert is_approved is False

    def test_parse_labels_assignee(self, mock_token):
        """Extracts assignee:* labels correctly."""
        client = GitHubClient("owner", "repo")
        labels = [
            {"name": "assignee:alice-chen"},
            {"name": "status:pending"},
        ]

        status, assignee, priority, has_feedback, is_approved = client._parse_labels(
            labels
        )

        assert assignee == "alice-chen"
        assert status == "pending"

    def test_parse_labels_priority(self, mock_token):
        """Extracts priority:* labels correctly."""
        client = GitHubClient("owner", "repo")
        labels = [
            {"name": "priority:medium"},
        ]

        status, assignee, priority, has_feedback, is_approved = client._parse_labels(
            labels
        )

        assert priority == "medium"

    def test_parse_labels_feedback_requested(self, mock_token):
        """Detects status:feedback-requested as has_feedback=True."""
        client = GitHubClient("owner", "repo")
        labels = [{"name": "status:feedback-requested"}]

        status, assignee, priority, has_feedback, is_approved = client._parse_labels(
            labels
        )

        assert has_feedback is True
        assert is_approved is False
        # feedback-requested should not set status
        assert status is None

    def test_parse_labels_empty(self, mock_token):
        """Handles empty labels list."""
        client = GitHubClient("owner", "repo")
        labels = []

        status, assignee, priority, has_feedback, is_approved = client._parse_labels(
            labels
        )

        assert status is None
        assert assignee is None
        assert priority is None
        assert has_feedback is False
        assert is_approved is False

    def test_parse_labels_all_fields(self, mock_token):
        """Extracts all label types correctly."""
        client = GitHubClient("owner", "repo")
        labels = [
            {"name": "status:in-review"},
            {"name": "assignee:bob-smith"},
            {"name": "priority:low"},
            {"name": "type:bug"},  # Should be ignored
        ]

        status, assignee, priority, has_feedback, is_approved = client._parse_labels(
            labels
        )

        assert status == "in-review"
        assert assignee == "bob-smith"
        assert priority == "low"
        assert has_feedback is False
        assert is_approved is False

    def test_parse_reviewer_label(self, mock_token):
        """Extracts reviewer:* labels correctly."""
        client = GitHubClient("owner", "repo")
        labels = [
            {"name": "reviewer:rachel-review"},
            {"name": "status:in-review"},
        ]

        reviewer = client._parse_reviewer_label(labels)

        assert reviewer == "rachel-review"

    def test_parse_reviewer_label_none(self, mock_token):
        """Returns None when no reviewer label exists."""
        client = GitHubClient("owner", "repo")
        labels = [{"name": "status:pending"}]

        reviewer = client._parse_reviewer_label(labels)

        assert reviewer is None

    def test_extract_linked_issue_closes(self, mock_token):
        """Extracts issue number from 'Closes #N'."""
        client = GitHubClient("owner", "repo")

        result = client._extract_linked_issue("This PR Closes #42 and does stuff")

        assert result == 42

    def test_extract_linked_issue_fixes(self, mock_token):
        """Extracts issue number from 'Fixes #N'."""
        client = GitHubClient("owner", "repo")

        result = client._extract_linked_issue("Fixes #123")

        assert result == 123

    def test_extract_linked_issue_resolves(self, mock_token):
        """Extracts issue number from 'Resolves #N'."""
        client = GitHubClient("owner", "repo")

        result = client._extract_linked_issue("resolves #99")

        assert result == 99

    def test_extract_linked_issue_none(self, mock_token):
        """Returns None when no linked issue found."""
        client = GitHubClient("owner", "repo")

        result = client._extract_linked_issue("Just a regular PR description")

        assert result is None

    def test_extract_linked_issue_empty_body(self, mock_token):
        """Returns None for empty/None body."""
        client = GitHubClient("owner", "repo")

        assert client._extract_linked_issue(None) is None
        assert client._extract_linked_issue("") is None


# -----------------------------------------------------------------------------
# TestIssueMethods
# -----------------------------------------------------------------------------


class TestIssueMethods:
    """Tests for issue-related API methods."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_issue(self, client):
        """get_issue returns issue data."""
        respx.get("https://api.github.com/repos/test-owner/test-repo/issues/1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 1,
                    "title": "Test Issue",
                    "state": "open",
                    "body": "Description",
                },
            )
        )

        result = await client.get_issue(1)

        assert result["number"] == 1
        assert result["title"] == "Test Issue"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_issue_node_id(self, client):
        """get_issue_node_id returns GraphQL node ID."""
        respx.post("https://api.github.com/graphql").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"repository": {"issue": {"id": "I_kwDOTest123"}}}},
            )
        )

        result = await client.get_issue_node_id(1)

        assert result == "I_kwDOTest123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_sub_issues(self, client):
        """get_sub_issues returns list of sub-issues via GraphQL."""
        respx.post("https://api.github.com/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "issue": {
                                "subIssues": {
                                    "nodes": [
                                        {
                                            "number": 2,
                                            "title": "Sub-issue 1",
                                            "state": "OPEN",
                                            "labels": {
                                                "nodes": [{"name": "status:pending"}]
                                            },
                                        },
                                        {
                                            "number": 3,
                                            "title": "Sub-issue 2",
                                            "state": "CLOSED",
                                            "labels": {"nodes": []},
                                        },
                                    ]
                                }
                            }
                        }
                    }
                },
            )
        )

        result = await client.get_sub_issues(1)

        assert len(result) == 2
        assert result[0]["number"] == 2
        assert result[0]["title"] == "Sub-issue 1"
        assert result[1]["number"] == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_issue(self, client):
        """create_issue creates a new issue."""
        route = respx.post(
            "https://api.github.com/repos/test-owner/test-repo/issues"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "number": 10,
                    "title": "New Issue",
                    "body": "Body text",
                },
            )
        )

        result = await client.create_issue("New Issue", "Body text", labels=["bug"])

        assert result["number"] == 10
        # Verify request body
        request = route.calls[0].request
        import json

        body = json.loads(request.content)
        assert body["title"] == "New Issue"
        assert body["body"] == "Body text"
        assert body["labels"] == ["bug"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_update_issue_labels(self, client):
        """update_issue_labels modifies labels correctly."""
        # First GET to get current labels
        respx.get("https://api.github.com/repos/test-owner/test-repo/issues/1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 1,
                    "labels": [{"name": "status:pending"}, {"name": "priority:high"}],
                },
            )
        )
        # Then PATCH to update
        patch_route = respx.patch(
            "https://api.github.com/repos/test-owner/test-repo/issues/1"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 1,
                    "labels": [
                        {"name": "status:in-progress"},
                        {"name": "priority:high"},
                    ],
                },
            )
        )

        result = await client.update_issue_labels(
            1,
            add_labels=["status:in-progress"],
            remove_labels=["status:pending"],
        )

        assert result["number"] == 1
        # Verify PATCH body contains expected labels
        import json

        request = patch_route.calls[0].request
        body = json.loads(request.content)
        labels = set(body["labels"])
        assert "status:in-progress" in labels
        assert "priority:high" in labels
        assert "status:pending" not in labels

    @pytest.mark.asyncio
    @respx.mock
    async def test_close_issue(self, client):
        """close_issue sets state to closed."""
        route = respx.patch(
            "https://api.github.com/repos/test-owner/test-repo/issues/1"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"number": 1, "state": "closed"},
            )
        )

        result = await client.close_issue(1)

        assert result["state"] == "closed"
        import json

        request = route.calls[0].request
        body = json.loads(request.content)
        assert body["state"] == "closed"

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_sub_issue(self, client):
        """add_sub_issue links sub-issue to parent via GraphQL mutation."""
        respx.post("https://api.github.com/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "addSubIssue": {
                            "issue": {"number": 1, "title": "Epic"},
                            "subIssue": {"number": 2, "title": "Task"},
                        }
                    }
                },
            )
        )

        result = await client.add_sub_issue("I_parent123", "I_sub456")

        assert result["issue"]["number"] == 1
        assert result["subIssue"]["number"] == 2


# -----------------------------------------------------------------------------
# TestPRMethods
# -----------------------------------------------------------------------------


class TestPRMethods:
    """Tests for PR-related API methods."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pr(self, client):
        """get_pr returns PR data."""
        respx.get("https://api.github.com/repos/test-owner/test-repo/pulls/5").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 5,
                    "title": "Add feature",
                    "state": "open",
                    "merged": False,
                    "user": {"login": "alice"},
                    "head": {"ref": "feature-branch"},
                    "base": {"ref": "main"},
                },
            )
        )

        result = await client.get_pr(5)

        assert result["number"] == 5
        assert result["title"] == "Add feature"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_prs(self, client):
        """list_prs returns list of PRs."""
        respx.get("https://api.github.com/repos/test-owner/test-repo/pulls").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"number": 5, "title": "PR 5"},
                    {"number": 6, "title": "PR 6"},
                ],
            )
        )

        result = await client.list_prs(state="open", per_page=10)

        assert len(result) == 2
        assert result[0]["number"] == 5

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pr_reviews(self, client):
        """get_pr_reviews returns list of reviews."""
        respx.get(
            "https://api.github.com/repos/test-owner/test-repo/pulls/5/reviews"
        ).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 1, "state": "COMMENTED", "user": {"login": "bob"}},
                    {"id": 2, "state": "APPROVED", "user": {"login": "carol"}},
                ],
            )
        )

        result = await client.get_pr_reviews(5)

        assert len(result) == 2
        assert result[1]["state"] == "APPROVED"

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pr_review_approve(self, client):
        """create_pr_review creates an approval review."""
        route = respx.post(
            "https://api.github.com/repos/test-owner/test-repo/pulls/5/reviews"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"id": 123, "state": "APPROVED"},
            )
        )

        result = await client.create_pr_review(5, "APPROVE", "LGTM!")

        assert result["state"] == "APPROVED"
        import json

        request = route.calls[0].request
        body = json.loads(request.content)
        assert body["event"] == "APPROVE"
        assert body["body"] == "LGTM!"

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pr_review_request_changes(self, client):
        """create_pr_review creates a request-changes review."""
        respx.post(
            "https://api.github.com/repos/test-owner/test-repo/pulls/5/reviews"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"id": 124, "state": "CHANGES_REQUESTED"},
            )
        )

        result = await client.create_pr_review(
            5, "REQUEST_CHANGES", "Please fix the tests"
        )

        assert result["state"] == "CHANGES_REQUESTED"


# -----------------------------------------------------------------------------
# TestHighLevelMethods
# -----------------------------------------------------------------------------


class TestHighLevelMethods:
    """Tests for get_epic_status, get_sub_issue_status, get_pr_status."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_epic_status(self, client):
        """get_epic_status combines issue and sub-issues correctly."""
        # Mock get_issue
        respx.get("https://api.github.com/repos/test-owner/test-repo/issues/1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 1,
                    "title": "Epic: Build Feature X",
                    "state": "open",
                    "body": "Epic description",
                },
            )
        )
        # Mock get_sub_issues (GraphQL)
        respx.post("https://api.github.com/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "issue": {
                                "subIssues": {
                                    "nodes": [
                                        {
                                            "number": 2,
                                            "title": "Task 1",
                                            "state": "OPEN",
                                            "labels": {
                                                "nodes": [
                                                    {"name": "status:in-progress"},
                                                    {"name": "assignee:alice"},
                                                    {"name": "priority:high"},
                                                ]
                                            },
                                        },
                                        {
                                            "number": 3,
                                            "title": "Task 2",
                                            "state": "CLOSED",
                                            "labels": {
                                                "nodes": [{"name": "status:pending"}]
                                            },
                                        },
                                    ]
                                }
                            }
                        }
                    }
                },
            )
        )

        result = await client.get_epic_status(1)

        assert isinstance(result, EpicStatus)
        assert result.number == 1
        assert result.title == "Epic: Build Feature X"
        assert result.total_sub_issues == 2
        assert result.completed_sub_issues == 1

        # Check sub-issues
        assert len(result.sub_issues) == 2
        sub1 = result.sub_issues[0]
        assert isinstance(sub1, SubIssueStatus)
        assert sub1.number == 2
        assert sub1.sf_status == "in-progress"
        assert sub1.assignee == "alice"
        assert sub1.priority == "high"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_sub_issue_status_found(self, client):
        """get_sub_issue_status returns correct sub-issue."""
        respx.post("https://api.github.com/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "issue": {
                                "subIssues": {
                                    "nodes": [
                                        {
                                            "number": 2,
                                            "title": "Task 1",
                                            "state": "OPEN",
                                            "labels": {
                                                "nodes": [{"name": "status:pending"}]
                                            },
                                        },
                                        {
                                            "number": 3,
                                            "title": "Task 2",
                                            "state": "OPEN",
                                            "labels": {
                                                "nodes": [
                                                    {"name": "status:in-progress"},
                                                    {"name": "assignee:bob"},
                                                ]
                                            },
                                        },
                                    ]
                                }
                            }
                        }
                    }
                },
            )
        )

        result = await client.get_sub_issue_status(1, 3)

        assert isinstance(result, SubIssueStatus)
        assert result.number == 3
        assert result.title == "Task 2"
        assert result.sf_status == "in-progress"
        assert result.assignee == "bob"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_sub_issue_status_not_found(self, client):
        """get_sub_issue_status raises error for missing sub-issue."""
        respx.post("https://api.github.com/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "issue": {
                                "subIssues": {
                                    "nodes": [
                                        {
                                            "number": 2,
                                            "title": "Task 1",
                                            "state": "OPEN",
                                            "labels": {"nodes": []},
                                        },
                                    ]
                                }
                            }
                        }
                    }
                },
            )
        )

        with pytest.raises(GitHubClientError) as exc_info:
            await client.get_sub_issue_status(1, 99)

        assert "#99" in str(exc_info.value)
        assert "#1" in str(exc_info.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pr_status(self, client):
        """get_pr_status combines PR and labels correctly."""
        # Mock get_pr
        respx.get("https://api.github.com/repos/test-owner/test-repo/pulls/5").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 5,
                    "title": "Add feature",
                    "state": "open",
                    "merged": False,
                    "mergeable": True,
                    "mergeable_state": "clean",
                    "user": {"login": "alice"},
                    "head": {"ref": "feature-branch"},
                    "base": {"ref": "main"},
                    "body": "Closes #2\n\nAdds the feature.",
                    "labels": [
                        {"name": "assignee:alice"},
                        {"name": "reviewer:rachel"},
                        {"name": "status:approved"},
                    ],
                },
            )
        )

        result = await client.get_pr_status(5)

        assert isinstance(result, PRStatus)
        assert result.number == 5
        assert result.title == "Add feature"
        assert result.state == "open"
        assert result.assignee == "alice"
        assert result.reviewer == "rachel"
        assert result.has_feedback is False
        assert result.is_approved is True
        assert result.mergeable is True
        assert result.has_conflicts is False
        assert result.linked_issue == 2
        assert result.head_branch == "feature-branch"
        assert result.base_branch == "main"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pr_status_with_feedback(self, client):
        """get_pr_status detects feedback-requested label."""
        respx.get("https://api.github.com/repos/test-owner/test-repo/pulls/5").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 5,
                    "title": "Add feature",
                    "state": "open",
                    "merged": False,
                    "user": {"login": "alice"},
                    "head": {"ref": "fix"},
                    "base": {"ref": "main"},
                    "labels": [
                        {"name": "status:feedback-requested"},
                    ],
                },
            )
        )

        result = await client.get_pr_status(5)

        assert result.has_feedback is True
        assert result.is_approved is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pr_status_merged(self, client):
        """get_pr_status reports merged state correctly."""
        respx.get("https://api.github.com/repos/test-owner/test-repo/pulls/5").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 5,
                    "title": "Add feature",
                    "state": "closed",
                    "merged": True,
                    "user": {"login": "alice"},
                    "head": {"ref": "fix"},
                    "base": {"ref": "main"},
                    "labels": [],
                },
            )
        )

        result = await client.get_pr_status(5)

        assert result.state == "merged"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pr_status_with_conflicts(self, client):
        """get_pr_status detects merge conflicts."""
        respx.get("https://api.github.com/repos/test-owner/test-repo/pulls/5").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 5,
                    "title": "Add feature",
                    "state": "open",
                    "merged": False,
                    "mergeable": False,
                    "mergeable_state": "dirty",
                    "user": {"login": "alice"},
                    "head": {"ref": "fix"},
                    "base": {"ref": "main"},
                    "labels": [],
                },
            )
        )

        result = await client.get_pr_status(5)

        assert result.mergeable is False
        assert result.has_conflicts is True


# -----------------------------------------------------------------------------
# TestActivityParsing
# -----------------------------------------------------------------------------


class TestActivityParsing:
    """Tests for parse_activity_comment."""

    @pytest.mark.asyncio
    async def test_parse_activity_valid(self, client):
        """parse_activity_comment parses valid activity format."""
        comment = {
            "id": 12345,
            "created_at": "2026-02-27T10:30:00Z",
            "body": (
                "**[2026-02-27 10:30 UTC] Alice Chen** (Programmer)\n"
                "**Event:** `claimed`\n"
                "**Issue:** #3\n"
                "**Message:** Starting work on trigonometric functions"
            ),
        }

        result = await client.parse_activity_comment(comment)

        assert result is not None
        assert result.agent_name == "Alice Chen"
        assert result.agent_type == "programmer"
        assert result.event_type == "claimed"
        assert result.issue_number == 3
        assert result.pr_number is None
        assert result.message == "Starting work on trigonometric functions"
        assert result.comment_id == 12345

    @pytest.mark.asyncio
    async def test_parse_activity_with_pr(self, client):
        """parse_activity_comment parses PR reference."""
        comment = {
            "id": 12346,
            "created_at": "2026-02-27T11:00:00Z",
            "body": (
                "**[2026-02-27 11:00 UTC] Bob Smith** (Reviewer)\n"
                "**Event:** `review_submitted`\n"
                "**PR:** #5\n"
                "**Message:** Approved with minor suggestions"
            ),
        }

        result = await client.parse_activity_comment(comment)

        assert result is not None
        assert result.agent_name == "Bob Smith"
        assert result.agent_type == "reviewer"
        assert result.event_type == "review_submitted"
        assert result.pr_number == 5
        assert result.issue_number is None

    @pytest.mark.asyncio
    async def test_parse_activity_invalid_format(self, client):
        """parse_activity_comment returns None for non-activity comments."""
        comment = {
            "id": 99999,
            "created_at": "2026-02-27T12:00:00Z",
            "body": "Just a regular comment on the epic.",
        }

        result = await client.parse_activity_comment(comment)

        assert result is None

    @pytest.mark.asyncio
    async def test_parse_activity_partial_format(self, client):
        """parse_activity_comment handles partial matches gracefully."""
        comment = {
            "id": 88888,
            "created_at": "2026-02-27T13:00:00Z",
            "body": "**Not a valid activity** (but looks similar)",
        }

        result = await client.parse_activity_comment(comment)

        assert result is None
