# AGENTS.md - Agent Guidelines for softfoundry

This document provides instructions for AI coding agents working in this repository.

## Project Overview

**softfoundry** employs multiple AI agents (Manager, Programmer, Reviewer) that collaborate to generate complete software projects end-to-end using `claude-agent-sdk` and GitHub for coordination.

- **Package Manager**: uv (https://docs.astral.sh/uv/)
- **Python Version**: 3.12+
- **Source Layout**: `src/softfoundry/`

## Architecture

The system uses GitHub as the central coordination mechanism:
- **Tasks** are tracked as GitHub Issues with labels
- **Programmers** work in git worktrees for parallel development
- **PRs** are created for each task and reviewed before merging
- **Status files** at `~/.softfoundry/agents/` enable health monitoring

## Directory Structure

```
softfoundry/
├── src/softfoundry/           # Main package source code
│   ├── __init__.py            # Package entry point
│   ├── agents/                # Agent implementations
│   │   ├── __init__.py
│   │   ├── manager.py         # Manager agent (coordinates project)
│   │   ├── programmer.py      # Programmer agent (implements tasks)
│   │   └── reviewer.py        # Reviewer agent (reviews and merges PRs)
│   └── utils/                 # Shared utilities
│       ├── __init__.py
│       ├── output.py          # Rich message formatting
│       ├── sessions.py        # Session persistence
│       └── status.py          # Agent status file management
├── castings/                  # Generated project workspaces
│   ├── {project}/             # Main git clone
│   ├── {project}-{name}/      # Programmer worktrees
├── docs/                      # Documentation
│   └── IMPLEMENTATION_PLAN.md # Full system design
├── pyproject.toml             # Project configuration
└── uv.lock                    # Dependency lock file

~/.softfoundry/                # User-level data
├── sessions/                  # Session persistence files
│   ├── manager-{project}.json
│   ├── programmer-{name}-{project}.json
│   └── reviewer-{project}.json
└── agents/                    # Agent status files
    └── {project}/
        ├── manager.status
        ├── programmer-{name}.status
        └── reviewer.status
```

## Build/Run Commands

```bash
# Install dependencies
uv sync

# Run the application
uv run softfoundry

# Add dependencies
uv add <package-name>
uv add --dev <package-name>
```

## Testing Commands

```bash
# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_example.py

# Run a specific test function
uv run pytest tests/test_example.py::test_function_name

# Run with verbose output
uv run pytest -v

# Run tests matching a pattern
uv run pytest -k "pattern"
```

## Linting and Formatting

```bash
# Lint with ruff
uv run ruff check .
uv run ruff check --fix .

# Format with ruff
uv run ruff format .

# Type checking
uv run pyright
```

## Code Style Guidelines

### Imports

Order imports in three groups separated by blank lines:
1. Standard library
2. Third-party
3. Local application

```python
import asyncio
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query

from softfoundry.agents.programmer import main
```

Use absolute imports. Avoid `from module import *`.

### Formatting

- **Line length**: 88 characters
- **Indentation**: 4 spaces
- **Quotes**: Double quotes for strings
- **Trailing commas**: Use in multi-line structures

### Type Hints

Always use type hints for function signatures. Use modern Python 3.12+ syntax:
- `list[str]` not `List[str]`
- `dict[str, int]` not `Dict[str, int]`
- `str | None` not `Optional[str]`

```python
def process_task(name: str, timeout: int = 30) -> dict[str, Any]:
    ...
```

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Functions/methods | snake_case | `process_message()` |
| Variables | snake_case | `user_input` |
| Classes | PascalCase | `AgentConfig` |
| Constants | UPPER_SNAKE_CASE | `MAX_RETRIES` |
| Private | Leading underscore | `_internal_helper()` |

### Error Handling

- Use specific exception types, not bare `except:`
- Provide meaningful error messages
- Chain exceptions with `raise ... from e`

### Docstrings and Data Structures

- Use Google-style docstrings (Args, Returns, Raises sections)
- Prefer `dataclasses` or `pydantic` for data structures

## Project-Specific Patterns

### Agent Implementation

Agents in `src/softfoundry/agents/` use `claude_agent_sdk`:

```python
async def run_my_agent(task: str, workspace: str) -> None:
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Glob", "Bash"],
        permission_mode="acceptEdits",
        system_prompt=f"System prompt for {workspace}",
    )
    async for message in query(prompt=task, options=options):
        ...
```

### Running Agents

```bash
# Run the manager agent (interactive - prompts for repo and num programmers)
uv run python -m softfoundry.agents.manager

# Run the manager with all options specified
uv run python -m softfoundry.agents.manager \
    --github-repo owner/repo \
    --clone-path castings/myproject \
    --num-programmers 2

# Run a programmer agent (spawned by manager, not usually run manually)
uv run python -m softfoundry.agents.programmer \
    --name "Alice Chen" \
    --github-repo owner/repo \
    --clone-path castings/myproject \
    --project myproject

# Run the reviewer agent (spawned by manager, not usually run manually)
uv run python -m softfoundry.agents.reviewer \
    --github-repo owner/repo \
    --clone-path castings/myproject \
    --project myproject
```

**Manager CLI Options:**
- `--github-repo` - GitHub repository (OWNER/REPO format, prompted if not provided)
- `--clone-path` - Local path to clone repo (default: castings/{project})
- `--num-programmers` - Number of programmer agents (prompted if not provided)
- `--verbosity` - Output level: minimal, medium, verbose (default: medium)
- `--max-iterations` - Safety limit for loop iterations (default: 100)
- `--resume` - Automatically resume existing session
- `--new-session` - Start fresh, deleting any existing session

**Programmer CLI Options:**
- `--name` - Programmer name (required, e.g., "Alice Chen")
- `--github-repo` - GitHub repository (required)
- `--clone-path` - Path to main git clone (required)
- `--project` - Project name (required)
- `--verbosity`, `--max-iterations`, `--resume`, `--new-session` - Same as manager

**Reviewer CLI Options:**
- `--github-repo` - GitHub repository (required)
- `--clone-path` - Path to main git clone (required)
- `--project` - Project name (required)
- `--verbosity`, `--max-iterations`, `--resume`, `--new-session` - Same as manager

### GitHub Label Schema

The manager creates these labels on project setup:

| Label | Color | Purpose |
|-------|-------|---------|
| `assignee:{name}` | `#0366d6` | Task assignment |
| `status:pending` | `#fbca04` | Not started |
| `status:in-progress` | `#0e8a16` | Being worked on |
| `status:in-review` | `#6f42c1` | PR awaiting review |
| `priority:high` | `#d73a4a` | High priority |
| `priority:medium` | `#fbca04` | Medium priority |
| `priority:low` | `#0e8a16` | Low priority |

### Status File Format

Agents maintain status files at `~/.softfoundry/agents/{project}/`:

```json
{
  "agent_type": "programmer",
  "name": "Alice Chen",
  "project": "scicalc",
  "pid": 12345,
  "status": "working",
  "details": "Implementing issue #3: Add trigonometric functions",
  "current_issue": 3,
  "current_pr": null,
  "last_update": "2026-02-13T14:30:00Z",
  "started_at": "2026-02-13T14:00:00Z"
}
```

**Status values:**
- `starting` - Initializing
- `idle` - Waiting for work
- `working` - Actively implementing
- `waiting_review` - PR created, waiting for review
- `addressing_feedback` - Addressing PR feedback
- `exited:success` - Completed all work
- `exited:error` - Crashed/errored
- `exited:terminated` - Killed by user or manager

### Castings Directory

The `castings/` directory contains generated project workspaces:
- `castings/{project}/` - Main git clone
- `castings/{project}-{name-slug}/` - Programmer worktrees (e.g., `castings/scicalc-alice-chen/`)

## Git Conventions

Use conventional commits:
- `feat:` New features
- `fix:` Bug fixes
- `docs:` Documentation
- `refactor:` Refactoring
- `test:` Tests
- `chore:` Maintenance
