# softfoundry Architecture

This document describes the detailed architecture of softfoundry, a multi-agent system for generating complete software projects end-to-end.

## Table of Contents

1. [System Overview](#system-overview)
2. [Core Components](#core-components)
3. [Agent Architecture](#agent-architecture)
4. [Coordination Mechanisms](#coordination-mechanisms)
5. [Data Flow](#data-flow)
6. [State Management](#state-management)
7. [Communication Patterns](#communication-patterns)
8. [Error Handling and Recovery](#error-handling-and-recovery)
9. [Security Considerations](#security-considerations)

---

## System Overview

softfoundry is designed around three specialized AI agents that work together to implement software projects:

```
┌─────────────────────────────────────────────────────────────────┐
│                          GitHub                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │    Issues    │  │     PRs      │  │    Labels    │          │
│  │  (Tasks)     │  │   (Code)     │  │  (Status)    │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
           │                  │                  │
           │    GitHub CLI    │     GitHub CLI   │
           │      (gh)        │       (gh)       │
           ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Local Machine                               │
│                                                                  │
│   ┌─────────┐      ┌────────────┐      ┌──────────┐            │
│   │ Manager │◄────►│ Programmer │◄────►│ Reviewer │            │
│   │  Agent  │      │   Agents   │      │  Agent   │            │
│   └────┬────┘      └─────┬──────┘      └────┬─────┘            │
│        │                 │                   │                  │
│        │     Status Files (health monitoring)│                  │
│        ▼                 ▼                   ▼                  │
│   ~/.softfoundry/agents/{project}/                              │
│                                                                  │
│   ┌─────────────────────────────────────────────────────┐       │
│   │                   castings/                          │       │
│   │  ┌─────────────┐  ┌─────────────────────────────┐   │       │
│   │  │  {project}/ │  │  {project}-{programmer}/    │   │       │
│   │  │ (main repo) │  │       (worktrees)           │   │       │
│   │  └─────────────┘  └─────────────────────────────┘   │       │
│   └─────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

### Design Principles

1. **GitHub as Single Source of Truth**: All task state, code changes, and coordination happen through GitHub Issues and PRs
2. **Loose Coupling**: Agents communicate indirectly through GitHub and status files, not direct messaging
3. **Crash Recovery**: Sessions and status files enable agents to resume after interruptions
4. **Parallel Development**: Git worktrees allow multiple programmers to work simultaneously
5. **Human-in-the-Loop**: The user can monitor progress and intervene when needed

---

## Core Components

### 1. Agents (`src/softfoundry/agents/`)

#### Manager Agent (`manager.py`)

The orchestrator responsible for project setup and monitoring.

**Responsibilities:**
- Project initialization (cloning, creating PROJECT.md)
- Task planning (creating GitHub issues)
- Label management (status, priority, assignee)
- Guiding user to start other agents
- Health monitoring via status files

**Key Characteristics:**
- Runs continuously in a loop
- 60-second polling interval for monitoring
- Can detect stale agents (>5 minutes without update)
- Exits when all issues are closed

```python
# Core loop structure
async with ClaudeSDKClient(options=options) as client:
    await client.query(initial_prompt)
    while iteration < max_iterations:
        async for message in client.receive_response():
            # Process messages
        if needs_user_input(last_assistant_text):
            user_input = read_multiline_input()
            await client.query(user_input)
        else:
            await asyncio.sleep(POLL_INTERVAL)
            await client.query("Continue monitoring...")
```

#### Programmer Agent (`programmer.py`)

Implements assigned tasks and creates PRs.

**Responsibilities:**
- Finding assigned issues (or picking up unassigned ones)
- Creating/managing git worktrees
- Implementing code changes
- Creating and updating PRs
- Addressing review feedback

**Key Characteristics:**
- Works in isolated worktree: `castings/{project}-{name-slug}/`
- Updates issue labels as it progresses
- 30-second polling interval when idle/waiting
- Named programmers: Alice Chen, Bob Smith, Carol Davis, etc.

**Workflow:**
```
1. Find pending issue assigned to self
   └─► No assigned? Find unassigned pending issue
       └─► No pending? Check if project complete
2. Create worktree and branch
3. Update issue: status:pending → status:in-progress
4. Implement changes
5. Commit, push, create PR
6. Update issue: status:in-progress → status:in-review
7. Wait for review
   └─► Changes requested? Address feedback, push
   └─► Approved and merged? Go to step 1
```

#### Reviewer Agent (`reviewer.py`)

Reviews PRs and merges approved code.

**Responsibilities:**
- Finding open PRs to review
- Analyzing code changes
- Approving or requesting changes
- Merging approved PRs

**Key Characteristics:**
- Works in main clone directory
- Reviews all PRs, not assigned to specific ones
- 30-second polling interval when no PRs
- Uses squash merges with branch deletion

**Review Criteria:**
- Correctness (matches issue requirements)
- Bug detection (logic errors, edge cases)
- Code quality (clean, readable)
- Style conformance
- Test coverage (if applicable)

### 2. Utilities (`src/softfoundry/utils/`)

#### Status Management (`status.py`)

Manages agent health monitoring through status files.

```python
STATUS_DIR = Path.home() / ".softfoundry" / "agents"
STALE_THRESHOLD_SECONDS = 300  # 5 minutes

# Key functions
get_status_path(project, agent_type, name) -> Path
update_status(status_path, status, details, **extra) -> None
read_status(status_path) -> dict | None
is_agent_stale(status_path, threshold=300) -> bool
is_agent_exited(status_path) -> bool
get_agent_pid(status_path) -> int | None
```

**Status File Format:**
```json
{
  "agent_type": "programmer",
  "name": "Alice Chen",
  "project": "scicalc",
  "pid": 12345,
  "status": "working",
  "details": "Implementing issue #3",
  "current_issue": 3,
  "current_pr": null,
  "last_update": "2026-02-13T14:30:00Z",
  "started_at": "2026-02-13T14:00:00Z"
}
```

**Atomic Writes:**
Status updates use atomic writes (write to temp file, then rename) to prevent corruption.

#### Session Management (`sessions.py`)

Enables crash recovery through session persistence.

```python
SESSIONS_DIR = Path.home() / ".softfoundry" / "sessions"

@dataclass
class SessionInfo:
    session_id: str
    agent_name: str
    agent_type: str
    project: str
    last_run: str
    num_turns: int
    total_cost_usd: float | None = None

class SessionManager:
    def get_session(agent_type, agent_name) -> SessionInfo | None
    def save_session(session_info) -> None
    def delete_session(agent_type, agent_name) -> bool
```

Session files are named: `{agent_type}-{name-slug}-{project}.json`

#### Output Formatting (`output.py`)

Rich console output with configurable verbosity.

```python
class Verbosity(Enum):
    MINIMAL = "minimal"   # Tool names only
    MEDIUM = "medium"     # Tool names + key parameters
    VERBOSE = "verbose"   # Full input/output

class MessagePrinter:
    def print_message(message: Any) -> None
```

Handles all SDK message types:
- `AssistantMessage` - Claude's responses
- `UserMessage` - User input
- `SystemMessage` - System events
- `ResultMessage` - Completion with cost/usage

#### LLM Utilities (`llm.py`)

Uses Claude (Haiku) for classification tasks.

```python
CLASSIFICATION_MODEL = "claude-3-5-haiku-latest"

def needs_user_input(text: str) -> bool
    """Detect if text asks a question requiring user input."""

def extract_question(text: str) -> str | None
    """Extract the main question from text."""
```

This enables agents to determine when to wait for user input vs. continue autonomously.

#### Input Handling (`input.py`)

Multi-line input for user responses.

```python
def read_multiline_input(prompt: str = "> ") -> str
    """Read lines until empty line submitted."""
```

### 3. CLI (`src/softfoundry/cli/`)

#### Clear Command (`clear.py`)

Cleans up sessions and status files.

```bash
softfoundry-clear              # Clear all
softfoundry-clear --project X  # Clear specific project
softfoundry-clear --dry-run    # Preview only
```

---

## Agent Architecture

### Agent Lifecycle

```
┌────────────────────────────────────────────────────────────┐
│                     AGENT LIFECYCLE                         │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────┐  ┌───────────┐  ┌─────────┐  ┌────────────┐  │
│  │ starting│─►│  working  │─►│  idle   │─►│exited:     │  │
│  └─────────┘  └───────────┘  └─────────┘  │success     │  │
│                     │                      └────────────┘  │
│                     │                                       │
│                     ▼                                       │
│  ┌───────────────────┐    ┌─────────────────────┐         │
│  │  waiting_review   │───►│ addressing_feedback │         │
│  └───────────────────┘    └─────────────────────┘         │
│                                                             │
│  Error states: exited:error, exited:terminated             │
└────────────────────────────────────────────────────────────┘
```

### Common Agent Pattern

All agents follow a similar pattern:

```python
async def run_agent(...):
    # 1. Initialize status file
    status_path = get_status_path(project, agent_type, name)
    update_status(status_path, "starting", "Initializing")
    
    # 2. Session management
    session_manager = SessionManager(project)
    existing_session = session_manager.get_session(...)
    # Handle resume/new session
    
    # 3. Check for crash recovery
    existing_status = read_status(status_path)
    resume_context = ""
    if existing_status and existing_status.get("current_issue"):
        resume_context = f"Previously working on #{issue}..."
    
    # 4. Build options and connect
    options = ClaudeAgentOptions(
        allowed_tools=[...],
        permission_mode="acceptEdits",
        system_prompt=system_prompt,
        resume=current_session_id,
        cwd=working_directory,
    )
    
    # 5. Main loop
    async with ClaudeSDKClient(options=options) as client:
        await client.query(initial_prompt)
        
        while iteration < max_iterations:
            async for message in client.receive_response():
                # Handle messages
                if isinstance(message, ResultMessage):
                    session_manager.save_session(...)
            
            if needs_user_input(last_text):
                user_input = read_multiline_input()
                await client.query(user_input)
            else:
                await asyncio.sleep(POLL_INTERVAL)
                await client.query("Continue...")
```

### Signal Handling

All agents implement graceful shutdown:

```python
class GracefulExit(Exception): pass
class ImmediateExit(Exception): pass

def setup_signal_handlers():
    state = {"shutdown_requested": False, "query_running": False}
    
    def handler(signum, frame):
        if state["shutdown_requested"]:
            raise ImmediateExit()  # Second Ctrl+C
        else:
            state["shutdown_requested"] = True
            if state["query_running"]:
                print("Waiting for query to complete...")
            else:
                raise GracefulExit()
    
    signal.signal(signal.SIGINT, handler)
    return state
```

---

## Coordination Mechanisms

### GitHub Labels

Labels are the primary coordination mechanism:

| Label Pattern | Purpose |
|---------------|---------|
| `status:pending` | Task available for work |
| `status:in-progress` | Task being implemented |
| `status:in-review` | PR created, awaiting review |
| `assignee:{slug}` | Task assigned to programmer |
| `priority:{level}` | Task priority (high/medium/low) |

### Task Assignment Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    TASK ASSIGNMENT FLOW                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Manager creates issue with:                                 │
│  ├─ status:pending                                          │
│  ├─ priority:medium                                         │
│  └─ assignee:alice-chen (optional)                          │
│                                                              │
│  Programmer queries for work:                               │
│  ├─ First: issues with own assignee label + status:pending  │
│  ├─ Then: issues with status:pending (no assignee)          │
│  └─ If none: check if all issues closed → exit              │
│                                                              │
│  Programmer starts work:                                     │
│  ├─ Removes: status:pending                                 │
│  └─ Adds: status:in-progress                                │
│                                                              │
│  Programmer creates PR:                                      │
│  ├─ Removes: status:in-progress                             │
│  └─ Adds: status:in-review                                  │
│                                                              │
│  Reviewer merges PR:                                         │
│  └─ Issue closed automatically via "Closes #N"              │
└─────────────────────────────────────────────────────────────┘
```

### Git Worktrees

Each programmer works in isolation:

```
castings/
├── myproject/              # Main clone (manager's workspace)
│   ├── .git/
│   └── src/
├── myproject-alice-chen/   # Alice's worktree
│   └── src/
└── myproject-bob-smith/    # Bob's worktree
    └── src/
```

**Worktree Management:**
```bash
# Create worktree with branch
git worktree add ../myproject-alice-chen -b feature/issue-3-auth origin/main

# After PR merged, clean up
git worktree remove ../myproject-alice-chen --force
git branch -D feature/issue-3-auth
```

---

## Data Flow

### Issue to Merged PR

```
┌──────────────────────────────────────────────────────────────────┐
│                      DATA FLOW: ISSUE TO PR                       │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  1. Manager creates issue                                         │
│     └─► gh issue create --title "..." --body "..." --label ...  │
│                                                                   │
│  2. Programmer finds issue                                        │
│     └─► gh issue list --label "assignee:alice-chen" ...         │
│                                                                   │
│  3. Programmer creates worktree + branch                          │
│     └─► git worktree add ... -b feature/issue-3-auth            │
│                                                                   │
│  4. Programmer updates labels                                     │
│     └─► gh issue edit 3 --remove-label pending --add-label ...  │
│                                                                   │
│  5. Programmer implements (Edit, Write tools)                     │
│     └─► Claude modifies files in worktree                        │
│                                                                   │
│  6. Programmer commits + pushes                                   │
│     └─► git add . && git commit && git push -u origin ...       │
│                                                                   │
│  7. Programmer creates PR                                         │
│     └─► gh pr create --title "..." --body "Closes #3"           │
│                                                                   │
│  8. Reviewer reviews PR                                           │
│     └─► gh pr diff 5 (analyze changes)                          │
│                                                                   │
│  9. Reviewer approves + merges                                    │
│     └─► gh pr review 5 --approve && gh pr merge 5 --squash      │
│                                                                   │
│ 10. Issue auto-closed by "Closes #3"                              │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## State Management

### Distributed State

State is distributed across multiple locations:

| State Type | Location | Purpose |
|------------|----------|---------|
| Task State | GitHub Issues | Task status, assignment, priority |
| Code State | GitHub PRs + Branches | Changes, review status |
| Agent Health | Status Files | Heartbeat, current task |
| Session State | Session Files | Claude conversation history |
| File State | Git Worktrees | Working copies |

### Consistency Model

The system uses eventual consistency:

1. Agents read GitHub state at start of each iteration
2. Make decisions based on current state
3. Update GitHub atomically (single `gh` command)
4. Other agents see changes on next poll

**Race Conditions:**
- Two programmers might try to claim same task
- First to update labels wins
- Loser will find a different task next iteration

---

## Communication Patterns

### Indirect Communication

Agents don't communicate directly. All coordination happens through:

1. **GitHub Labels**: Task status and assignment
2. **GitHub Comments**: Progress updates, review feedback
3. **Status Files**: Health monitoring (manager reads these)
4. **PRs**: Code transfer from programmer to reviewer

### User Interaction

The manager handles user interaction:

```
┌─────────────────────────────────────────────┐
│           USER INTERACTION                   │
├─────────────────────────────────────────────┤
│                                              │
│  Claude's Response                           │
│       │                                      │
│       ▼                                      │
│  needs_user_input(text) ──► LLM classifier  │
│       │                                      │
│       ├─► YES: Wait for user input          │
│       │        read_multiline_input()        │
│       │        Send response to Claude       │
│       │                                      │
│       └─► NO: Continue autonomously          │
│               Sleep, then poll               │
└─────────────────────────────────────────────┘
```

---

## Error Handling and Recovery

### Crash Recovery Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    CRASH RECOVERY                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Agent starts                                                │
│       │                                                      │
│       ▼                                                      │
│  Check for existing session file                             │
│       │                                                      │
│       ├─► Found + --resume: Load session_id                 │
│       │                                                      │
│       ├─► Found + prompt: Ask user to resume                │
│       │                                                      │
│       └─► Found + --new-session: Delete session             │
│                                                              │
│  Check existing status file                                  │
│       │                                                      │
│       ├─► status = "exited:*": Start fresh                  │
│       │                                                      │
│       └─► status = other + current_issue:                   │
│           Add resume context to prompt                       │
│           "You were working on #3..."                        │
│                                                              │
│  Claude SDK handles conversation resume                      │
│  via session_id in ClaudeAgentOptions.resume                │
└─────────────────────────────────────────────────────────────┘
```

### Error States

| Status | Meaning | Recovery |
|--------|---------|----------|
| `exited:success` | Completed normally | None needed |
| `exited:error` | Crashed with exception | Restart agent |
| `exited:terminated` | User stopped (Ctrl+C) | Restart if needed |

### Stale Agent Detection

```python
def is_agent_stale(status_path, threshold=300):
    data = read_status(status_path)
    if not data:
        return True
    last_update = datetime.fromisoformat(data["last_update"])
    age = (datetime.now() - last_update).total_seconds()
    return age > threshold  # Default 5 minutes
```

---

## Security Considerations

### Permission Model

Agents run with `permission_mode="acceptEdits"` which auto-accepts file changes. This is appropriate for:
- Controlled development environments
- Trusted repositories
- Supervised operation

**For production use:**
- Review generated code before merging
- Use branch protection rules
- Require human approval for PRs

### GitHub Access

Agents use `gh` CLI with the user's authentication:
- User must be authenticated (`gh auth login`)
- User's permissions limit agent capabilities
- All actions are auditable via GitHub

### Status Files

Status files are stored in user's home directory:
- Only accessible by the user
- Contain no secrets (just metadata)
- Can be cleaned with `softfoundry-clear`

---

## Extension Points

### Adding New Agents

To add a new agent type:

1. Create `src/softfoundry/agents/newagent.py`
2. Follow the common agent pattern (see above)
3. Define system prompt and workflow
4. Add CLI entry point in `pyproject.toml`

### Custom Tools

The SDK supports custom tools via `create_sdk_mcp_server()`. See `docs/ClaudeAgentSDK.md` for details.

### Hooks

The SDK supports hooks for intercepting tool calls. This could be used for:
- Logging all commands
- Restricting certain operations
- Modifying tool inputs
