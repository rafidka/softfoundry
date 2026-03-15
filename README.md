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
- Anthropic API key (for question detection)
- Claude Code OAuth token (for the agent SDK)

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd softfoundry

# Install dependencies
uv sync

# Copy environment template and configure
cp .env.example .env
# Edit .env and add your API keys:
# - SOFTFOUNDRY_ANTHROPIC_API_KEY: Get from https://console.anthropic.com/settings/keys
# - SOFTFOUNDRY_CLAUDE_CODE_OAUTH_TOKEN: Generate with `claude --setup-token`

# Ensure gh is authenticated
gh auth status
```

## Quick Start

### 1. Start the Manager

```bash
sf manager
```

The manager will:
- Prompt for the GitHub repository (e.g., `myuser/myproject`)
- Clone the repository to `castings/{project}/`
- Check for `PROJECT.md` (collaborate with you to create it if missing)
- Create a top-level epic issue and sub-issues for each task
- Provide commands to start programmer and reviewer agents
- Monitor progress until completion

### 2. With All Options Specified

```bash
sf manager \
    --github-repo myuser/myproject \
    --clone-path castings/myproject \
    --epic 42
```

### 3. Start Programmer and Reviewer Agents

After the manager completes setup, start the agents in separate terminals:

```bash
# Terminal 2: Programmer 1
sf programmer \
    --name "Alice Chen" \
    --github-repo myuser/myproject \
    --clone-path castings/myproject \
    --project myproject \
    --epic 42

# Terminal 3: Programmer 2
sf programmer \
    --name "Bob Smith" \
    --github-repo myuser/myproject \
    --clone-path castings/myproject \
    --project myproject \
    --epic 42

# Terminal 4: Reviewer
sf reviewer \
    --name "Rachel Review" \
    --github-repo myuser/myproject \
    --clone-path castings/myproject \
    --project myproject \
    --epic 42
```

## How It Works

### Phase 1: Setup

1. Manager clones the GitHub repository
2. Reads or creates `PROJECT.md` describing the project
3. Creates a top-level epic issue with sub-issues for each task
4. Creates status, priority, and assignee labels

### Phase 2: Work

1. Manager instructs user to start programmer and reviewer agents
2. Programmers self-assign unassigned sub-issues (pull-based model)
3. Each programmer works in their own git worktree
4. Programmers create PRs when tasks are complete
5. Reviewers self-assign PRs, review code, request changes or approve and merge

### Phase 3: Monitoring

1. Manager monitors agent health via status files and heartbeats
2. Detects stale agents and releases their tasks
3. Tracks epic progress and detects project completion when all sub-issues are closed

## CLI Reference

### Manager

| Option | Description |
|--------|-------------|
| `--github-repo` | GitHub repository (OWNER/REPO format, prompted if not provided) |
| `--clone-path` | Local path to clone repo (default: castings/{project}) |
| `--epic` | GitHub issue number to use as the top-level epic |
| `--verbosity` | Output level: minimal, medium, verbose (default: medium) |
| `--max-iterations` | Safety limit for loop iterations (default: 100) |
| `--session` | Session mode: auto, resume, or new (default: auto) |

### Programmer

| Option | Description |
|--------|-------------|
| `--name` | Programmer name (required, e.g., "Alice Chen") |
| `--github-repo` | GitHub repository (required) |
| `--clone-path` | Path to main git clone (required) |
| `--project` | Project name (required) |
| `--epic` | GitHub issue number of the epic to work on (required) |
| `--task-delay` | Seconds to wait between task runs (default: 60) |
| `--verbosity`, `--max-iterations`, `--session` | Same as manager |

### Reviewer

| Option | Description |
|--------|-------------|
| `--name` | Reviewer name (required, e.g., "Rachel Review") |
| `--github-repo` | GitHub repository (required) |
| `--clone-path` | Path to main git clone (required) |
| `--project` | Project name (required) |
| `--epic` | GitHub issue number of the epic to work on (required) |
| `--task-delay` | Seconds to wait between review runs (default: 60) |
| `--verbosity`, `--max-iterations`, `--session` | Same as manager |

### Utility Commands

```bash
# Clear all sessions and status files
sf clear

# Clear files for a specific project
sf clear --project myproject

# Preview what would be deleted
sf clear --dry-run
```

## Project Structure

```
softfoundry/
├── src/softfoundry/
│   ├── agents/           # Agent implementations
│   │   ├── base.py       # Agent loop framework (base class)
│   │   ├── manager.py    # Manager agent (coordinates project)
│   │   ├── memory.py     # Agent memory file management
│   │   ├── programmer.py # Programmer agent (implements tasks)
│   │   ├── prompts.py    # Shared prompt builders
│   │   ├── reviewer.py   # Reviewer agent (reviews and merges PRs)
│   │   └── sessions.py   # Session persistence
│   ├── cli/              # CLI commands
│   │   ├── clear.py      # Clear sessions and status files
│   │   ├── debug.py      # Debug subcommands for orchestrator tools
│   │   ├── manager.py    # Manager CLI command
│   │   ├── programmer.py # Programmer CLI command
│   │   └── reviewer.py   # Reviewer CLI command
│   ├── mcp/              # MCP servers
│   │   ├── orchestrator.py # GitHub coordination
│   │   ├── user_server.py  # User interaction (ask_user)
│   │   ├── github_client.py # Async GitHub API client
│   │   ├── constants.py    # Shared constants
│   │   └── types.py        # Shared types
│   ├── tui/              # Textual TUI
│   │   ├── app.py        # Main Textual App
│   │   ├── styles/       # TUI stylesheets
│   │   └── widgets/      # TUI widget components
│   └── utils/            # Shared utilities
│       ├── env.py        # Environment variable loading (.env)
│       ├── github.py     # GitHub label colors, GraphQL helpers
│       ├── llm.py        # LLM utilities
│       └── status.py     # Agent status management
├── castings/             # Generated project workspaces
│   ├── {project}/        # Main git clone
│   └── {project}-{name}/ # Programmer worktrees
├── .env.example          # Environment template
├── ARCHITECTURE.md       # Detailed system architecture
├── MCP_TOOLS.md          # MCP tool documentation by agent
├── claude-docs/          # Claude Agent SDK reference
└── pyproject.toml

~/.softfoundry/           # User-level data
├── sessions/             # Session persistence (crash recovery)
└── agents/               # Agent status and memory files
    └── {project}/
        ├── manager.status
        ├── programmer-{name-slug}.status
        ├── programmer-{name-slug}.memory.md
        ├── reviewer-{name-slug}.status
        └── reviewer-{name-slug}.memory.md
```

## GitHub Label Schema

The manager creates these labels on project setup:

| Label | Purpose |
|-------|---------|
| `type:epic` | Top-level epic issue containing sub-tasks |
| `assignee:{name}` | Task assignment (e.g., `assignee:alice-chen`) |
| `reviewer:{name}` | PR reviewer assignment (e.g., `reviewer:rachel-review`) |
| `status:pending` | Task not started |
| `status:in-progress` | Task being worked on |
| `status:in-review` | PR awaiting review |
| `status:feedback-requested` | Reviewer requested changes on PR |
| `status:approved` | Reviewer approved PR (ready to merge) |
| `priority:high/medium/low` | Task priority |

## Session Management

### Resume a Session

If an agent crashes or is interrupted, it can resume from where it left off:

```bash
# Resume automatically
sf manager --session resume

# Will prompt if a session exists (default behavior)
sf manager
```

### Start Fresh

To discard an existing session and start over:

```bash
sf manager --session new
```

### Clean Up

To remove all session and status files:

```bash
sf clear
```

## Agent Health Monitoring

Agents write status files to `~/.softfoundry/agents/{project}/` that include:

- Current status (working, idle, waiting_review, etc.)
- Current issue or PR being worked on
- Last update timestamp
- Process ID

The manager can detect stale agents (no update in 5+ minutes) and alert the user.

## Environment Configuration

softfoundry uses a `.env` file for API credentials with `SOFTFOUNDRY_*` prefixed variable names to avoid conflicts with system environment variables:

| Variable | Purpose |
|----------|---------|
| `SOFTFOUNDRY_ANTHROPIC_API_KEY` | Direct Anthropic API calls |
| `SOFTFOUNDRY_CLAUDE_CODE_OAUTH_TOKEN` | Claude Code SDK authentication |

The system will:
1. Warn about and ignore any system `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`
2. Load credentials from `.env` using the prefixed names
3. Validate all required variables before starting

## Textual TUI

Agents feature a split-pane terminal UI built with Textual:
- Scrollable message stream with markdown rendering and collapsible tool blocks
- Sidebar with agent status, session info, task, and epic progress
- Input area at the bottom — answers agent questions or interrupts with free-text
- `ask_user` / `ask_user_choice` MCP tools let agents explicitly request user input
- Press Ctrl+C to exit gracefully

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
