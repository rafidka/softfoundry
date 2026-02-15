# softfoundry

A multi-agent system for generating complete software projects end-to-end using Claude and the `claude-agent-sdk`.

## Overview

softfoundry employs multiple AI agents (Manager, Programmer, Reviewer) that collaborate to generate complete software projects. The system uses GitHub as the central coordination mechanism:

- **Manager** - Sets up the project, creates GitHub issues for tasks, spawns programmer/reviewer agents, and monitors progress
- **Programmers** - Work on assigned issues in git worktrees, create PRs when done
- **Reviewer** - Reviews PRs, provides feedback or approves, merges approved code

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [GitHub CLI](https://cli.github.com/) (`gh`) - authenticated with repo access
- Claude API access (via `claude-agent-sdk`)

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd softfoundry

# Install dependencies
uv sync

# Ensure gh is authenticated
gh auth status
```

## Quick Start

### 1. Start the Manager

```bash
uv run python -m softfoundry.agents.manager
```

The manager will:
- Prompt for the GitHub repository (e.g., `myuser/myproject`)
- Prompt for number of programmers
- Clone the repository to `castings/{project}/`
- Check for `PROJECT.md` (collaborate with you to create it if missing)
- Create GitHub issues for each task
- Spawn programmer and reviewer agents
- Monitor progress until completion

### 2. With All Options Specified

```bash
uv run python -m softfoundry.agents.manager \
    --github-repo myuser/myproject \
    --clone-path castings/myproject \
    --num-programmers 2
```

## How It Works

1. **Setup Phase**
   - Manager clones the GitHub repository
   - Reads or creates `PROJECT.md` describing the project
   - Creates labeled GitHub issues for each task
   - Creates assignee labels for each programmer

2. **Work Phase**
   - Manager spawns programmer agents (each in their own worktree)
   - Manager spawns reviewer agent
   - Programmers pick up assigned tasks, implement them, create PRs
   - Reviewer reviews PRs, requests changes or approves and merges

3. **Monitoring Phase**
   - Manager monitors agent health via status files
   - Restarts any stale or crashed agents
   - Detects project completion when all issues are closed

## CLI Options

### Manager

| Option | Description |
|--------|-------------|
| `--github-repo` | GitHub repository (OWNER/REPO format) |
| `--clone-path` | Local path to clone repo (default: castings/{project}) |
| `--num-programmers` | Number of programmer agents |
| `--verbosity` | Output level: minimal, medium, verbose |
| `--max-iterations` | Safety limit for loop iterations (default: 100) |
| `--resume` | Resume existing session |
| `--new-session` | Start fresh, deleting existing session |

### Programmer

| Option | Description |
|--------|-------------|
| `--name` | Programmer name (required, e.g., "Alice Chen") |
| `--github-repo` | GitHub repository (required) |
| `--clone-path` | Path to main git clone (required) |
| `--project` | Project name (required) |
| `--verbosity`, `--max-iterations`, `--resume`, `--new-session` | Same as manager |

### Reviewer

| Option | Description |
|--------|-------------|
| `--github-repo` | GitHub repository (required) |
| `--clone-path` | Path to main git clone (required) |
| `--project` | Project name (required) |
| `--verbosity`, `--max-iterations`, `--resume`, `--new-session` | Same as manager |

## Project Structure

```
softfoundry/
├── src/softfoundry/
│   ├── agents/           # Manager, Programmer, Reviewer agents
│   └── utils/            # Shared utilities (output, sessions, status)
├── castings/             # Generated project workspaces
│   ├── {project}/        # Main git clone
│   └── {project}-{name}/ # Programmer worktrees
├── docs/
│   └── IMPLEMENTATION_PLAN.md  # Full system design
└── pyproject.toml

~/.softfoundry/           # User-level data
├── sessions/             # Session persistence
└── agents/               # Agent status files
    └── {project}/
        ├── manager.status
        ├── programmer-{name}.status
        └── reviewer.status
```

## GitHub Label Schema

The manager creates these labels on project setup:

| Label | Purpose |
|-------|---------|
| `assignee:{name}` | Task assignment (e.g., `assignee:alice-chen`) |
| `status:pending` | Task not started |
| `status:in-progress` | Task being worked on |
| `status:in-review` | PR awaiting review |
| `priority:high/medium/low` | Task priority |

## Development

```bash
# Run linting
uv run ruff check .

# Run formatting
uv run ruff format .

# Run type checking
uv run pyright

# Run tests
uv run pytest
```

## License

MIT
