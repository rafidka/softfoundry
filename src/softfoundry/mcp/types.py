"""Type definitions for the MCP orchestration package."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# Status values from labels
IssueStatusLabel = Literal["pending", "in-progress", "in-review"]
PriorityLabel = Literal["high", "medium", "low"]
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
    state: str  # open, closed (GitHub issue state)
    sf_status: str | None  # softfoundry workflow status from labels:
    # pending, in-progress, in-review. None when issue is closed.
    assignee: str | None  # from assignee:* label
    reviewer: str | None = None  # from reviewer:* label on the linked PR
    priority: str | None  # high, medium, low (from priority:* label)
    linked_pr: int | None  # PR number if one exists
    depends_on: list[int] = []  # issue numbers this task depends on


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
    is_approved: bool  # True if status:approved label exists
    mergeable: bool
    has_conflicts: bool
    linked_issue: int | None  # from "Closes #N" or "Fixes #N"
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
