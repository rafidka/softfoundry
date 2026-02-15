"""LLM utilities for softfoundry.

This module provides utilities for interacting with the Anthropic API
directly (not via Claude Code) for simple classification tasks.
"""

import anthropic

# Use a fast, cheap model for classification
CLASSIFICATION_MODEL = "claude-3-5-haiku-latest"


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

    client = anthropic.Anthropic()

    prompt = f"""Analyze the following text and determine if it ends with a question or request that requires the user to provide information, make a decision, or give input.

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

Does this text require the user to provide input or answer a question? Respond with only "YES" or "NO"."""

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


def extract_question(text: str) -> str | None:
    """Extract the specific question being asked from the text.

    Args:
        text: The text containing a question.

    Returns:
        The extracted question, or None if no clear question found.
    """
    if not text or not text.strip():
        return None

    # Truncate very long text
    max_chars = 4000
    if len(text) > max_chars:
        text = "..." + text[-max_chars:]

    client = anthropic.Anthropic()

    prompt = f"""Extract the main question or request from the following text that requires user input.

Return ONLY the question/request itself, without any additional text.
If there are multiple questions, return the most important one that the user needs to answer to proceed.
If there's no clear question, respond with "NONE".

Text:
<text>
{text}
</text>

Question:"""

    response = client.messages.create(
        model=CLASSIFICATION_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract the response text
    response_text = ""
    for block in response.content:
        if block.type == "text":
            response_text = block.text.strip()
            break

    if response_text.upper() == "NONE":
        return None

    return response_text
