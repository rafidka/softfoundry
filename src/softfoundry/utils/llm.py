"""LLM utilities for softfoundry.

This module provides utilities for interacting with the Anthropic API
directly (not via Claude Code) for simple classification tasks.
"""

import anthropic

from softfoundry.utils.env import get_anthropic_api_key

# Use a fast, cheap model for classification
CLASSIFICATION_MODEL = "claude-haiku-4-5"


def needs_user_input(text: str) -> bool:
    """Determine if the text contains a question requiring user input.

    Uses the Anthropic API to classify whether the given text is asking
    a question that requires the user to provide information or make a decision.

    Args:
        text: The text to analyze (typically the last assistant message).

    Returns:
        True if the text is asking a question that needs user input,
        False otherwise.
    """
    if not text or not text.strip():
        return False

    # Truncate very long text to focus on the end (where questions usually are)
    max_chars = 4000
    if len(text) > max_chars:
        text = "..." + text[-max_chars:]

    client = anthropic.Anthropic(api_key=get_anthropic_api_key())

    prompt = f"""
Analyze the following text and determine if it ends with a question or request that
requires the user to provide information, make a decision, or give input.

Examples of text that NEEDS user input:
- "What programming language should I use?"
- "Please tell me more about the features you want."
- "Should I proceed with option A or B?"
- "I need some information before continuing. What is the project's main purpose?"

Examples of text that does NOT need user input:
- "I've completed the task. The file has been created."
- "Here's the implementation you requested."
- "I'm now going to create the database schema."
- "Let me know if you have any questions." (this is a polite closing, not a real question)

Text to analyze:
<text>
{text}
</text>

Does this text require the user to provide input or answer a question? Respond with only "YES" or "NO".
""".strip()

    response = client.messages.create(
        model=CLASSIFICATION_MODEL,
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract the response text
    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text = block.text.strip().upper()
            break

    return response_text == "YES"
