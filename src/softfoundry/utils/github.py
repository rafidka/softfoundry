"""GitHub-related constants and utilities.

This module provides shared constants for GitHub labels, colors, and
helper functions for building GitHub CLI commands.
"""

# Label colors (without # prefix for gh CLI)
LABEL_COLORS = {
    # Assignment labels
    "assignee": "0366d6",  # Blue - task assignment
    "reviewer": "6f42c1",  # Purple - PR reviewer assignment
    # Status labels
    "status_pending": "fbca04",  # Yellow - not started
    "status_in_progress": "0e8a16",  # Green - being worked on
    "status_in_review": "6f42c1",  # Purple - PR awaiting review
    # Priority labels
    "priority_high": "d73a4a",  # Red
    "priority_medium": "fbca04",  # Yellow
    "priority_low": "0e8a16",  # Green
}


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
