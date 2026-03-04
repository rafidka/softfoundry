"""GitHub-related constants and utilities.

This module provides shared constants for GitHub labels, colors, and
helper functions for building GitHub CLI commands.
"""

# Label colors (without # prefix for gh CLI)
LABEL_COLORS = {
    # Type labels
    "type_epic": "5319e7",  # Purple - top-level epic/project issue
    # Assignment labels
    "assignee": "0366d6",  # Blue - task assignment
    "reviewer": "6f42c1",  # Purple - PR reviewer assignment
    # Status labels
    "status_pending": "fbca04",  # Yellow - not started
    "status_in_progress": "0e8a16",  # Green - being worked on
    "status_in_review": "6f42c1",  # Purple - PR awaiting review
    "status_feedback_requested": "d73a4a",  # Red - reviewer requested changes
    "status_approved": "0e8a16",  # Green - reviewer approved
    # Priority labels
    "priority_high": "d73a4a",  # Red
    "priority_medium": "fbca04",  # Yellow
    "priority_low": "0e8a16",  # Green
}


# GraphQL mutation templates for sub-issues
# These are used by agents via `gh api graphql`
GRAPHQL_GET_ISSUE_NODE_ID = """
query GetIssueNodeId($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      id
    }
  }
}
"""

GRAPHQL_ADD_SUB_ISSUE = """
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

GRAPHQL_LIST_SUB_ISSUES = """
query ListSubIssues($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      subIssues(first: 100) {
        nodes {
          number
          title
          state
        }
      }
    }
  }
}
"""


def get_label_color(label_type: str) -> str:
    """Get the color for a label type.

    Args:
        label_type: One of the keys in LABEL_COLORS.

    Returns:
        The color code (without #).

    Raises:
        KeyError: If label_type is not recognized.
    """
    return LABEL_COLORS[label_type]


def format_signature(name: str, role: str) -> str:
    """Format a signature for GitHub comments.

    Args:
        name: The agent's name (e.g., "Alice Chen").
        role: The agent's role (e.g., "Programmer", "Reviewer").

    Returns:
        Formatted signature string like "**[Alice Chen - Programmer]:**"
    """
    return f"**[{name} - {role}]:**"


def format_inline_signature(name: str) -> str:
    """Format a short signature for inline code comments.

    Args:
        name: The agent's name (e.g., "Rachel Review").

    Returns:
        Short signature like "[Rachel Review]"
    """
    return f"[{name}]"


def build_get_issue_node_id_command(repo: str, issue_number: int) -> str:
    """Build a gh api graphql command to get an issue's node ID.

    Args:
        repo: Repository in OWNER/REPO format.
        issue_number: The issue number.

    Returns:
        A gh CLI command string.
    """
    owner, name = repo.split("/")
    return (
        f"gh api graphql -f query='{GRAPHQL_GET_ISSUE_NODE_ID.strip()}' "
        f"-f owner='{owner}' -f repo='{name}' -F number={issue_number}"
    )


def build_add_sub_issue_command(parent_node_id: str, sub_issue_node_id: str) -> str:
    """Build a gh api graphql command to add a sub-issue to a parent.

    Args:
        parent_node_id: The GraphQL node ID of the parent issue.
        sub_issue_node_id: The GraphQL node ID of the sub-issue.

    Returns:
        A gh CLI command string.
    """
    return (
        f"gh api graphql -f query='{GRAPHQL_ADD_SUB_ISSUE.strip()}' "
        f"-f parentId='{parent_node_id}' -f subIssueId='{sub_issue_node_id}'"
    )


def build_list_sub_issues_command(repo: str, issue_number: int) -> str:
    """Build a gh api graphql command to list sub-issues of an issue.

    Args:
        repo: Repository in OWNER/REPO format.
        issue_number: The parent issue number.

    Returns:
        A gh CLI command string.
    """
    owner, name = repo.split("/")
    return (
        f"gh api graphql -f query='{GRAPHQL_LIST_SUB_ISSUES.strip()}' "
        f"-f owner='{owner}' -f repo='{name}' -F number={issue_number}"
    )
