"""Environment variable utilities with controlled loading from .env files.

This module provides environment initialization and getter functions for required API
keys. Unlike typical dotenv usage, this module requires explicit initialization to
ensure proper handling of system vs .env variables.

Key features:
- Warns about and clears system ANTHROPIC_API_KEY/CLAUDE_CODE_OAUTH_TOKEN
- Loads from .env using SOFTFOUNDRY_* prefixed variable names
- Validates all required variables are present before proceeding
"""

import os
import sys

from dotenv import load_dotenv
from rich.console import Console


class MissingEnvironmentVariable(Exception):
    """Raised when a required environment variable is not set."""

    pass


def initialize_environment() -> None:
    """Initialize environment from .env file and clear system API keys.

    This function must be called at program startup before any other env access.

    It performs the following steps:
    1. Checks for system ANTHROPIC_API_KEY/CLAUDE_CODE_OAUTH_TOKEN and warns if found
    2. Clears those system env vars to prevent Claude Agent SDK from using them
    3. Loads .env file
    4. Validates required SOFTFOUNDRY_* variables exist

    If .env is not found or required variables are missing, prints an error and exits
    with code 1.
    """
    # Step 1: Check and warn about system env vars that will be ignored
    system_vars = ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]
    warned_vars = []
    for var in system_vars:
        if os.environ.get(var):
            warned_vars.append(var)

    if warned_vars:
        console = Console(stderr=True)
        console.print(
            f"[yellow]Warning: Found system environment variables that will be "
            f"ignored: {', '.join(warned_vars)}[/yellow]"
        )
        console.print(
            "[yellow]Using values from .env file instead (SOFTFOUNDRY_* variables).[/yellow]"
        )

    # Step 2: Clear system env vars to prevent SDK from accidentally using them
    for var in system_vars:
        if var in os.environ:
            del os.environ[var]

    # Step 3: Load .env file (searches current and parent directories)
    found = load_dotenv()
    if not found:
        print("Error: .env file not found.", file=sys.stderr)
        print(
            "Create a .env file with SOFTFOUNDRY_ANTHROPIC_API_KEY and "
            "SOFTFOUNDRY_CLAUDE_CODE_OAUTH_TOKEN.",
            file=sys.stderr,
        )
        print("See .env.example for a template.", file=sys.stderr)
        sys.exit(1)

    # Step 4: Validate required variables are present
    missing = []
    if not os.getenv("SOFTFOUNDRY_ANTHROPIC_API_KEY"):
        missing.append("SOFTFOUNDRY_ANTHROPIC_API_KEY")
    if not os.getenv("SOFTFOUNDRY_CLAUDE_CODE_OAUTH_TOKEN"):
        missing.append("SOFTFOUNDRY_CLAUDE_CODE_OAUTH_TOKEN")

    if missing:
        print(
            f"Error: Missing required environment variables in .env: "
            f"{', '.join(missing)}",
            file=sys.stderr,
        )
        print("See .env.example for required variables.", file=sys.stderr)
        sys.exit(1)


def get_anthropic_api_key() -> str:
    """Get the Anthropic API key for direct API calls.

    This key is used by llm.py for lightweight tasks like question detection.

    Returns:
        The Anthropic API key.

    Raises:
        MissingEnvironmentVariable: If SOFTFOUNDRY_ANTHROPIC_API_KEY is not set.
            This usually means initialize_environment() was not called.
    """
    key = os.getenv("SOFTFOUNDRY_ANTHROPIC_API_KEY")
    if not key:
        raise MissingEnvironmentVariable(
            "SOFTFOUNDRY_ANTHROPIC_API_KEY environment variable is not set.\n\n"
            "Make sure initialize_environment() is called at program startup."
        )
    return key


def get_claude_code_token() -> str:
    """Get the Claude Code OAuth token for SDK authentication.

    This token is used by the Claude Agent SDK and is more cost-effective than using an
    API key directly.

    Returns:
        The Claude Code OAuth token.

    Raises:
        MissingEnvironmentVariable: If SOFTFOUNDRY_CLAUDE_CODE_OAUTH_TOKEN is not set.
            This usually means initialize_environment() was not called.
    """
    token = os.getenv("SOFTFOUNDRY_CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        raise MissingEnvironmentVariable(
            "SOFTFOUNDRY_CLAUDE_CODE_OAUTH_TOKEN environment variable is not set.\n\n"
            "Make sure initialize_environment() is called at program startup."
        )
    return token
