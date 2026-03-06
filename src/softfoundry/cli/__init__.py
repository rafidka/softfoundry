"""CLI entry point for softfoundry using Typer."""

import typer

from softfoundry.cli import clear, debug, manager, programmer, reviewer

app = typer.Typer(
    help="Multi-agent system for generating software projects end-to-end.",
    no_args_is_help=True,
)

manager.register_command(app)
programmer.register_command(app)
reviewer.register_command(app)
clear.register_command(app)
debug.register_command(app)

if __name__ == "__main__":
    app()
