"""Input utilities for softfoundry."""


def read_multiline_input(prompt: str = "> ") -> str:
    """Read multi-line input from the user.

    Reads lines until the user enters an empty line (just presses Enter).

    Args:
        prompt: The prompt to display for the first line.

    Returns:
        The concatenated input as a single string.
    """
    print(f"{prompt}(Enter an empty line to submit)")
    lines = []
    while True:
        try:
            line = input()
            if line == "":
                break
            lines.append(line)
        except EOFError:
            break
    return "\n".join(lines)
