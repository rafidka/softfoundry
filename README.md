# softfoundry

A multi-agent system for generating complete software projects end-to-end using Claude and the `claude-agent-sdk`.

## Overview

softfoundry employs multiple AI agents (Manager, Programmer, etc.) that collaborate to generate complete software projects. The Manager agent creates tasks from a project description, assigns them to Programmer agents, and monitors progress until completion.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Claude API access (via `claude-agent-sdk`)

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd softfoundry

# Install dependencies
uv sync
```

## Quick Start

### 1. Create a Project

Create a project directory structure:

```bash
mkdir -p castings/myproject-planning/tasks castings/myproject-planning/team castings/myproject
```

Create `castings/myproject-planning/PROJECT.md` with your project description.

### 2. Start the Manager Agent

```bash
uv run python -m softfoundry.agents.manager --project-dir castings/myproject
```

The manager will:
- Read `PROJECT.md` and create task files
- Wait for programmers to become available
- Assign tasks to available programmers
- Monitor progress until all tasks are complete

### 3. Start Programmer Agents (in separate terminals)

```bash
uv run python -m softfoundry.agents.programmer --name "Alice" --project-dir castings/myproject
uv run python -m softfoundry.agents.programmer --name "Bob" --project-dir castings/myproject
```

Each programmer will:
- Register as available in the team directory
- Wait for task assignments
- Work on assigned tasks
- Mark tasks complete and wait for more work

## CLI Options

Both agents support:

| Option | Description |
|--------|-------------|
| `--project-dir` | Path to the project directory (required) |
| `--name` | Agent name (default: "Alice Chen" / "John Doe") |
| `--verbosity` | Output level: minimal, medium, verbose (default: medium) |
| `--max-iterations` | Safety limit for loop iterations (default: 100) |
| `--resume` | Automatically resume existing session |
| `--new-session` | Start fresh, deleting any existing session |

## Project Structure

```
softfoundry/
├── src/softfoundry/
│   ├── agents/           # Manager and Programmer agents
│   └── utils/            # Shared utilities (output, sessions, state)
├── castings/             # Generated projects
│   ├── {project}/        # Project code
│   └── {project}-planning/
│       ├── PROJECT.md    # Project description
│       ├── tasks/        # Task files
│       └── team/         # Team member files
└── pyproject.toml
```

## Development

```bash
# Run linting
uv run ruff check .

# Run type checking
uv run pyright

# Run tests
uv run pytest
```

## License

MIT
