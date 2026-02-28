"""Type definitions for the MCP orchestration package."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# Status values from labels
IssueStatusLabel = Literal["pending", "in-progress", "in-review"]
PriorityLabel = Literal["high", "medium", "low"]
ReviewState = Literal["PENDING", "APPROVED", "CHANGES_REQUESTED", "COMMENTED"]
ActivityEventType = Literal[
    "started",
    "claimed",
    "progress",
    "pr_created",
    "review_started",
    "review_submitted",
    "feedback_addressed",
    "merged",
    "completed",
    "idle",
    "error",
]


class SubIssueStatus(BaseModel):
    """Status of a sub-issue within an epic."""

    number: int
    title: str
    state: str  # open, closed
    status: str | None  # pending, in-progress, in-review (from status:* label)
    assignee: str | None  # from assignee:* label
    priority: str | None  # high, medium, low (from priority:* label)
    linked_pr: int | None  # PR number if one exists


class EpicStatus(BaseModel):
    """Status of an epic with its sub-issues."""

    number: int
    title: str
    state: str  # open, closed
    body: str
    sub_issues: list[SubIssueStatus]
    total_sub_issues: int
    completed_sub_issues: int


class PRStatus(BaseModel):
    """Status of a pull request."""

    number: int
    title: str
    state: str  # open, closed, merged
    assignee: str | None  # from assignee:* label (agent who owns this PR)
    reviewer: str | None  # from reviewer:* label
    has_feedback: bool  # True if status:feedback-requested label exists
    mergeable: bool
    has_conflicts: bool
    linked_issue: int | None  # from "Closes #N" or "Fixes #N"
    review_state: str | None  # PENDING, APPROVED, CHANGES_REQUESTED, COMMENTED
    head_branch: str  # branch name
    base_branch: str  # target branch (usually main)


class ActivityEntry(BaseModel):
    """An activity log entry posted on the epic."""

    timestamp: datetime
    agent_name: str
    agent_type: str
    event_type: str  # started, claimed, progress, pr_created, etc.
    message: str
    issue_number: int | None
    pr_number: int | None
    comment_id: int  # GitHub comment ID
