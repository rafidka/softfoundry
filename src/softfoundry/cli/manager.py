"""Manager command for softfoundry CLI."""

import asyncio
from enum import Enum
from typing import Annotated

import typer

from softfoundry.agents.manager import DEFAULT_MAX_ITERATIONS, run_manager
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
    """Register the manager command with the Typer app."""

    @app.command(help="Run the manager agent for project coordination.")
    def manager(
        github_repo: Annotated[
            str | None,
            typer.Option(
                help="GitHub repository in OWNER/REPO format (prompted if not provided)"
            ),
        ] = None,
        clone_path: Annotated[
            str | None,
            typer.Option(
                help="Local path to clone the repo (default: castings/{project})"
            ),
        ] = None,
        num_programmers: Annotated[
            int | None,
            typer.Option(help="Number of programmer agents (prompted if not provided)"),
        ] = None,
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
                run_manager(
                    github_repo=github_repo,
                    clone_path=clone_path,
                    num_programmers=num_programmers,
                    verbosity=verbosity.value,
                    resume=resume,
                    new_session=new_session,
                    max_iterations=max_iterations,
                )
            )
        except KeyboardInterrupt:
            pass  # Clean exit, message already printed by agent

    return (manager,)
