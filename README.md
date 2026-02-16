# softfoundry

A multi-agent system for generating complete software projects end-to-end using Claude and the `claude-agent-sdk`.

## Overview

softfoundry employs multiple AI agents (Manager, Programmer, Reviewer) that collaborate to generate complete software projects. The system uses GitHub as the central coordination mechanism:

- **Manager** - Sets up the project, creates GitHub issues for tasks, guides the user to spawn programmer/reviewer agents, and monitors progress
- **Programmers** - Work on assigned issues in git worktrees, create PRs when done
- **Reviewer** - Reviews PRs, provides feedback or approves, merges approved code

## Key Features

- **Autonomous Development**: Agents work independently, picking up tasks and implementing them
- **GitHub-Native Coordination**: Tasks tracked as issues, code changes as PRs
- **Parallel Development**: Multiple programmers work in isolated git worktrees
- **Crash Recovery**: Sessions and status files enable resumption after interruptions
- **Health Monitoring**: Status files allow the manager to detect and restart failed agents

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
- Provide commands to start programmer and reviewer agents
- Monitor progress until completion

### 2. With All Options Specified

```bash
uv run python -m softfoundry.agents.manager \
    --github-repo myuser/myproject \
    --clone-path castings/myproject \
    --num-programmers 2
```

### 3. Start Programmer and Reviewer Agents

After the manager completes setup, start the agents in separate terminals:

```bash
# Terminal 2: Programmer 1
uv run python -m softfoundry.agents.programmer \
    --name "Alice Chen" \
    --github-repo myuser/myproject \
    --clone-path castings/myproject \
    --project myproject

# Terminal 3: Programmer 2
uv run python -m softfoundry.agents.programmer \
    --name "Bob Smith" \
    --github-repo myuser/myproject \
    --clone-path castings/myproject \
    --project myproject

# Terminal 4: Reviewer
uv run python -m softfoundry.agents.reviewer \
    --github-repo myuser/myproject \
    --clone-path castings/myproject \
    --project myproject
```

## How It Works

### Phase 1: Setup

1. Manager clones the GitHub repository
2. Reads or creates `PROJECT.md` describing the project
3. Creates labeled GitHub issues for each task
4. Creates assignee labels for each programmer

### Phase 2: Work

1. Manager instructs user to start programmer and reviewer agents
2. Programmers pick up assigned tasks (or help with unassigned ones)
3. Each programmer works in their own git worktree
4. Programmers create PRs when tasks are complete
5. Reviewer reviews PRs, requests changes or approves and merges

### Phase 3: Monitoring

1. Manager monitors agent health via status files
2. Checks GitHub for task completion status
3. Detects project completion when all issues are closed

## CLI Reference

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

### Utility Commands

```bash
# Clear all sessions and status files
uv run softfoundry-clear

# Clear files for a specific project
uv run softfoundry-clear --project myproject

# Preview what would be deleted
uv run softfoundry-clear --dry-run
```

## Project Structure

```
softfoundry/
├── src/softfoundry/
│   ├── agents/           # Manager, Programmer, Reviewer agents
│   ├── cli/              # CLI commands (clear)
│   └── utils/            # Shared utilities
│       ├── input.py      # Multi-line input handling
│       ├── llm.py        # Question detection using Claude
│       ├── output.py     # Rich console formatting
│       ├── sessions.py   # Session persistence
│       └── status.py     # Agent status management
├── castings/             # Generated project workspaces
│   ├── {project}/        # Main git clone
│   └── {project}-{name}/ # Programmer worktrees
├── ARCHITECTURE.md       # Detailed system architecture
├── claude-docs/          # Claude Agent SDK reference
│   └── IMPLEMENTATION_PLAN.md  # Original design document
└── pyproject.toml

~/.softfoundry/           # User-level data
├── sessions/             # Session persistence (crash recovery)
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

## Session Management

### Resume a Session

If an agent crashes or is interrupted, it can resume from where it left off:

```bash
# Resume automatically
uv run python -m softfoundry.agents.manager --resume

# Will prompt if a session exists
uv run python -m softfoundry.agents.manager
```

### Start Fresh

To discard an existing session and start over:

```bash
uv run python -m softfoundry.agents.manager --new-session
```

### Clean Up

To remove all session and status files:

```bash
uv run softfoundry-clear
```

## Agent Health Monitoring

Agents write status files to `~/.softfoundry/agents/{project}/` that include:

- Current status (working, idle, waiting_review, etc.)
- Current issue or PR being worked on
- Last update timestamp
- Process ID

The manager can detect stale agents (no update in 5+ minutes) and alert the user.

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

## Architecture

For detailed architecture documentation, see [ARCHITECTURE.md](ARCHITECTURE.md).

## License

MIT
