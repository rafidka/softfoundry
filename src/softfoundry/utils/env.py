"""Environment variable utilities with auto-loading from .env files.

This module automatically loads environment variables from a .env file
when imported, and provides getter functions for required API keys.
"""

import os

from dotenv import load_dotenv

# Auto-load .env on module import
# Searches current directory and parent directories
load_dotenv()


class MissingEnvironmentVariable(Exception):
    """Raised when a required environment variable is not set."""

    pass


def get_anthropic_api_key() -> str:
    """Get the Anthropic API key for direct API calls.

    This key is used by llm.py for lightweight tasks like question detection.

    Returns:
        The Anthropic API key.

    Raises:
        MissingEnvironmentVariable: If ANTHROPIC_API_KEY is not set.
    """
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise MissingEnvironmentVariable(
            """ANTHROPIC_API_KEY environment variable is not set.

This key is required for direct Anthropic API calls (e.g., question detection).

To fix this:
1. Create a .env file in the project root (or copy from .env.example)
2. Add your Anthropic API key:
   ANTHROPIC_API_KEY=sk-ant-...

You can get an API key from: https://console.anthropic.com/settings/keys"""
        )
    return key


def get_claude_code_token() -> str:
    """Get the Claude Code OAuth token for SDK authentication.

    This token is used by the Claude Agent SDK and is more cost-effective
    than using an API key directly.

    Returns:
        The Claude Code OAuth token.

    Raises:
        MissingEnvironmentVariable: If CLAUDE_CODE_OAUTH_TOKEN is not set.
    """
    token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        raise MissingEnvironmentVariable(
            """CLAUDE_CODE_OAUTH_TOKEN environment variable is not set.

This token is required for Claude Agent SDK authentication.
Using an OAuth token is more cost-effective than using an API key directly.

To fix this:
1. Generate a long-lived token by running:
   claude --setup-token

2. Add the token to your .env file:
   CLAUDE_CODE_OAUTH_TOKEN=your-token-here

Alternatively, you can set it as an environment variable in your shell."""
        )
    return token
