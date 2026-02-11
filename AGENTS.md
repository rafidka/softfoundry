# AGENTS.md - Agent Guidelines for softfoundry

This document provides instructions for AI coding agents working in this repository.

## Project Overview

**softfoundry** employs multiple AI agents (Planner, Programmer, Tester, Reviewer, etc.) that collaborate to generate complete software projects end-to-end using `claude-agent-sdk`.

- **Package Manager**: uv (https://docs.astral.sh/uv/)
- **Python Version**: 3.12+
- **Source Layout**: `src/softfoundry/`

## Directory Structure

```
softfoundry/
├── src/softfoundry/       # Main package source code
│   ├── __init__.py        # Package entry point
│   └── agents/            # Agent implementations
├── castings/              # Generated project workspaces (e.g., castings/arithmetic/)
├── pyproject.toml         # Project configuration
└── uv.lock                # Dependency lock file
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
        allowed_tools=["Read", "Edit", "Glob"],
        permission_mode="acceptEdits",
        system_prompt=f"System prompt for {workspace}",
    )
    async for message in query(prompt=task, options=options):
        ...
```

### Castings Directory

The `castings/` directory contains generated project workspaces.

## Git Conventions

Use conventional commits:
- `feat:` New features
- `fix:` Bug fixes
- `docs:` Documentation
- `refactor:` Refactoring
- `test:` Tests
- `chore:` Maintenance
