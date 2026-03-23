"""Fake session command for softfoundry CLI."""

import asyncio
from enum import Enum
from typing import Annotated

import typer

from softfoundry.agents.fake import run_fake_session


class Verbosity(str, Enum):
    """Output verbosity level."""

    minimal = "minimal"
    medium = "medium"
    verbose = "verbose"


def register_command(app: typer.Typer) -> tuple:
    """Register the fake session command with the Typer app."""

    @app.command(help="Run a fake agent session for TUI testing.")
    def fake(
        verbosity: Annotated[
            Verbosity,
            typer.Option(help="Output verbosity level"),
        ] = Verbosity.medium,
        step_delay: Annotated[
            float,
            typer.Option(help="Seconds to wait between scripted UI events"),
        ] = 0.6,
    ) -> None:
        if step_delay < 0:
            raise typer.BadParameter("--step-delay must be >= 0")

        try:
            asyncio.run(
                run_fake_session(
                    verbosity=verbosity.value,
                    step_delay=step_delay,
                )
            )
        except KeyboardInterrupt:
            pass

    return (fake,)
