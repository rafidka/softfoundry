# Orchestrator MCP Tools by Agent

## Epic / Issue Management


| Tool Name                   | Manager | Programmer | Reviewer |
| --------------------------- | ------- | ---------- | -------- |
| `get_epic_status`           | Yes     | Yes        | Yes      |
| `get_sub_issue`             | Yes     | Yes        | Yes      |
| `list_available_sub_issues` | --      | Yes        | --       |
| `list_my_sub_issues`        | --      | Yes        | --       |
| `claim_sub_issue`           | --      | Yes        | --       |
| `update_sub_issue_status`   | --      | Yes        | --       |
| `create_sub_issue`          | Yes     | --         | --       |
| `close_epic`                | Yes     | --         | --       |
| `create_issue`              | Yes     | --         | --       |
| `list_issues`               | Yes     | --         | --       |


## Pull Request Management


| Tool Name                 | Manager | Programmer | Reviewer |
| ------------------------- | ------- | ---------- | -------- |
| `get_pr_status`           | --      | Yes        | Yes      |
| `list_my_prs`             | --      | Yes        | --       |
| `list_my_reviews`         | --      | --         | Yes      |
| `list_open_prs`           | Yes     | --         | --       |
| `list_prs_for_review`     | --      | --         | Yes      |
| `claim_pr_review`         | --      | --         | Yes      |
| `request_changes`         | --      | --         | Yes      |
| `mark_feedback_addressed` | --      | Yes        | --       |
| `approve_pr`              | --      | --         | Yes      |
| `get_pr_feedback`         | --      | Yes        | Yes      |
| `get_pr_diff`             | --      | --         | Yes      |
| `create_pr`               | --      | Yes        | --       |
| `merge_pr`                | --      | Yes        | --       |


## Comments & Labels


| Tool Name             | Manager | Programmer | Reviewer |
| --------------------- | ------- | ---------- | -------- |
| `comment_on_issue`    | Yes     | Yes        | --       |
| `comment_on_pr`       | Yes     | Yes        | Yes      |
| `create_label`        | Yes     | Yes        | Yes      |
| `update_issue_labels` | Yes     | --         | --       |


## Activity Logging


| Tool Name          | Manager | Programmer | Reviewer |
| ------------------ | ------- | ---------- | -------- |
| `log_activity`     | Yes     | Yes        | Yes      |
| `get_activity_log` | Yes     | Yes        | Yes      |


## Summary


| Agent            | Tool Count |
| ---------------- | ---------- |
| **Manager**      | 13         |
| **Programmer**   | 17         |
| **Reviewer**     | 14         |
| **Total unique** | 29         |


### Notable Patterns

- 6 tools are shared by all agents: `get_epic_status`, `get_sub_issue`, `comment_on_pr`, `create_label`, `log_activity`, `get_activity_log`
- Manager is the only agent that creates/closes issues and manages labels directly
- Programmer is the only agent that claims tasks, creates/merges PRs, and addresses feedback
- Reviewer is the only agent that claims reviews, approves PRs, requests changes, and views diffs
- `comment_on_issue` is available to Manager and Programmer but not Reviewer — reviewers interact exclusively through PR-level tools

