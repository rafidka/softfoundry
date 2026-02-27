"""Reviewer command for softfoundry CLI."""

import asyncio
from enum import Enum
from typing import Annotated

import typer

from softfoundry.agents.reviewer import DEFAULT_MAX_ITERATIONS, run_reviewer
from softfoundry.utils.env import initialize_environment


class SessionMode(str, Enum):
    """Session handling mode."""

    auto = "auto"
    resume = "resume"
    new = "new"


class Verbosity(str, Enum):
    """Output verbosity level."""

    minimal = "minimal"
    medium = "medium"
    verbose = "verbose"


def register_command(app: typer.Typer) -> tuple:
    """Register the reviewer command with the Typer app."""

    @app.command(help="Run a reviewer agent for PR review and merging.")
    def reviewer(
        name: Annotated[
            str,
            typer.Option(help="Name of the reviewer (e.g., 'Rachel Review')"),
        ],
        github_repo: Annotated[
            str,
            typer.Option(help="GitHub repository in OWNER/REPO format"),
        ],
        clone_path: Annotated[
            str,
            typer.Option(help="Path to the main git clone"),
        ],
        project: Annotated[
            str,
            typer.Option(help="Project name"),
        ],
        verbosity: Annotated[
            Verbosity,
            typer.Option(help="Output verbosity level"),
        ] = Verbosity.medium,
        max_iterations: Annotated[
            int,
            typer.Option(help="Maximum loop iterations (safety limit)"),
        ] = DEFAULT_MAX_ITERATIONS,
        session: Annotated[
            SessionMode,
            typer.Option(help="Session mode: auto (prompt), resume, or new"),
        ] = SessionMode.auto,
    ) -> None:
        initialize_environment()

        # Convert session mode to resume/new_session flags
        resume = session == SessionMode.resume
        new_session = session == SessionMode.new

        try:
            asyncio.run(
                run_reviewer(
                    name=name,
                    github_repo=github_repo,
                    clone_path=clone_path,
                    project=project,
                    verbosity=verbosity.value,
                    resume=resume,
                    new_session=new_session,
                    max_iterations=max_iterations,
                )
            )
        except KeyboardInterrupt:
            pass  # Clean exit, message already printed by agent

    return (reviewer,)
