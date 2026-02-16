# AGENTS.md - Agent Guidelines for softfoundry

This document provides instructions for AI coding agents working in this repository.

## Project Overview

**softfoundry** is a multi-agent system that generates complete software projects end-to-end using AI. It employs three specialized agents (Manager, Programmer, Reviewer) that collaborate via GitHub Issues and Pull Requests, powered by the `claude-agent-sdk`.

- **Package Manager**: uv (https://docs.astral.sh/uv/)
- **Python Version**: 3.12+
- **Source Layout**: `src/softfoundry/`
- **Dependencies**: `claude-agent-sdk`, `anthropic`, `rich`

## Architecture

The system uses GitHub as the central coordination mechanism:
- **Tasks** are tracked as GitHub Issues with labels for status and assignment
- **Programmers** work in git worktrees for parallel development
- **PRs** are created for each task and reviewed before merging
- **Status files** at `~/.softfoundry/agents/{project}/` enable health monitoring
- **Sessions** at `~/.softfoundry/sessions/` enable conversation persistence and crash recovery

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
│   ├── cli/                   # CLI commands
│   │   ├── __init__.py
│   │   └── clear.py           # Clear sessions and status files
│   └── utils/                 # Shared utilities
│       ├── __init__.py
│       ├── input.py           # Multi-line input handling
│       ├── llm.py             # LLM utilities (question detection)
│       ├── output.py          # Rich message formatting
│       ├── sessions.py        # Session persistence
│       └── status.py          # Agent status file management
├── castings/                  # Generated project workspaces
│   ├── {project}/             # Main git clone
│   └── {project}-{name}/      # Programmer worktrees
├── ARCHITECTURE.md            # System architecture details
├── claude-docs/               # Claude Agent SDK reference
│   ├── ClaudeAgentSDK.md      # SDK reference
│   └── IMPLEMENTATION_PLAN.md # Implementation design
├── pyproject.toml             # Project configuration
└── uv.lock                    # Dependency lock file

~/.softfoundry/                # User-level data
├── sessions/                  # Session persistence files
│   ├── manager-{name}-{project}.json
│   ├── programmer-{name}-{project}.json
│   └── reviewer-reviewer-{project}.json
└── agents/                    # Agent status files
    └── {project}/
        ├── manager.status
        ├── programmer-{name-slug}.status
        └── reviewer.status
```

## Build/Run Commands

```bash
# Install dependencies
uv sync

# Run the main entry point
uv run softfoundry

# Clear all sessions and status files
uv run softfoundry-clear

# Clear files for a specific project
uv run softfoundry-clear --project myproject

# Dry run (see what would be deleted)
uv run softfoundry-clear --dry-run

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
import argparse
import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from softfoundry.utils.output import create_printer
from softfoundry.utils.sessions import SessionManager
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
- Prefer `dataclasses` for data structures

## Project-Specific Patterns

### Agent Implementation

Agents use `ClaudeSDKClient` from `claude_agent_sdk` for continuous conversations:

```python
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

async def run_agent(prompt: str) -> None:
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Glob", "Write", "Bash", "Grep"],
        permission_mode="acceptEdits",
        system_prompt="Your system prompt here",
        cwd="/path/to/workspace",
    )
    
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        
        async for message in client.receive_response():
            # Process messages
            if isinstance(message, ResultMessage):
                # Handle completion
                pass
```

### Signal Handling Pattern

All agents implement graceful shutdown with signal handlers:

```python
class GracefulExit(Exception):
    """Raised when user requests graceful shutdown."""
    pass

class ImmediateExit(Exception):
    """Raised when user requests immediate shutdown."""
    pass

def setup_signal_handlers() -> dict[str, bool]:
    """Set up signal handlers for graceful shutdown."""
    state = {"shutdown_requested": False, "query_running": False}

    def handler(signum: int, frame: object) -> None:
        if state["shutdown_requested"]:
            raise ImmediateExit()
        else:
            state["shutdown_requested"] = True
            if state["query_running"]:
                print("\nShutdown requested. Waiting for current query...")
            else:
                raise GracefulExit()

    signal.signal(signal.SIGINT, handler)
    return state
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

### CLI Options

**Manager:**
| Option | Description |
|--------|-------------|
| `--github-repo` | GitHub repository (OWNER/REPO format, prompted if not provided) |
| `--clone-path` | Local path to clone repo (default: castings/{project}) |
| `--num-programmers` | Number of programmer agents (prompted if not provided) |
| `--verbosity` | Output level: minimal, medium, verbose (default: medium) |
| `--max-iterations` | Safety limit for loop iterations (default: 100) |
| `--resume` | Automatically resume existing session |
| `--new-session` | Start fresh, deleting any existing session |

**Programmer:**
| Option | Description |
|--------|-------------|
| `--name` | Programmer name (required, e.g., "Alice Chen") |
| `--github-repo` | GitHub repository (required) |
| `--clone-path` | Path to main git clone (required) |
| `--project` | Project name (required) |
| `--verbosity`, `--max-iterations`, `--resume`, `--new-session` | Same as manager |

**Reviewer:**
| Option | Description |
|--------|-------------|
| `--github-repo` | GitHub repository (required) |
| `--clone-path` | Path to main git clone (required) |
| `--project` | Project name (required) |
| `--verbosity`, `--max-iterations`, `--resume`, `--new-session` | Same as manager |

### GitHub Label Schema

The manager creates these labels on project setup:

| Label | Color | Purpose |
|-------|-------|---------|
| `assignee:{name}` | `#0366d6` | Task assignment (e.g., `assignee:alice-chen`) |
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

### Utility Modules

**`utils/status.py`** - Agent status file management:
- `get_status_path()` - Get path to status file
- `update_status()` - Update status with atomic writes
- `read_status()` - Read status data
- `is_agent_stale()` - Check if agent is unresponsive (>5 min)
- `sanitize_name()` - Convert names to filename-safe slugs

**`utils/sessions.py`** - Session persistence:
- `SessionManager` - Manages session files for crash recovery
- `SessionInfo` - Dataclass for session metadata
- `format_session_info()` - Human-readable session display

**`utils/output.py`** - Rich console output:
- `MessagePrinter` - Prints SDK messages with configurable verbosity
- `Verbosity` - Enum (minimal, medium, verbose)
- `create_printer()` - Factory function

**`utils/llm.py`** - LLM utilities:
- `needs_user_input()` - Detect if agent is asking a question
- `extract_question()` - Extract the question from text

**`utils/input.py`** - Input handling:
- `read_multiline_input()` - Read multi-line user input

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
