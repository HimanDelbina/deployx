"""
DeployX Project Rollback Orchestrator
Rolls back a deployed project to its previous known-good commit and image.
"""

from __future__ import annotations

from typing import Optional
from rich.console import Console
from rich.panel import Panel

from deployx.core.security import validate_project_name
from deployx.deployment.deploy import run_deployment
from deployx.logging.logger import ProjectLogger
from deployx.state import get_state_manager


def rollback_project(
    project_name: str,
    build_timeout: Optional[int] = None,
    no_build_timeout: bool = False,
    verbose: bool = False,
    plain: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Rolls back a deployed project to its previous known-good commit and Docker image.
    """
    if console is None:
        console = Console()

    valid_name = validate_project_name(project_name)
    logger = ProjectLogger(valid_name)
    state_mgr = get_state_manager()
    state = state_mgr.get_state(valid_name)

    if not state or not state.previous_commit:
        msg = f"Project '{valid_name}' does not have a recorded previous commit to roll back to. No previous commit recorded."
        logger.error(msg)
        console.print(Panel(f"[bold red]Rollback unavailable:[/bold red] {msg}", title="Rollback Error", border_style="red"))
        return False

    prev_commit = state.previous_commit
    curr_commit = state.current_commit or "unknown"
    short_prev = prev_commit[:7]
    short_curr = curr_commit[:7]

    console.print(
        Panel(
            f"[bold yellow]Initiating rollback for '{valid_name}'[/bold yellow]\n\n"
            f"[bold]Current commit:[/bold]  {short_curr}\n"
            f"[bold]Target rollback:[/bold] {short_prev}\n"
            f"[bold]Target image:[/bold]    deployx_{valid_name}:{short_prev}",
            title="Rollback",
            border_style="yellow",
        )
    )
    logger.info(f"Initiating rollback from commit {curr_commit} to {prev_commit}")

    success = run_deployment(
        valid_name,
        target_commit=prev_commit,
        build_timeout=build_timeout,
        no_build_timeout=no_build_timeout,
        verbose=verbose,
        plain=plain,
        console=console,
    )

    if success:
        logger.info(f"Rollback to {prev_commit} completed successfully.")
        console.print(f"[bold green]Rollback completed successfully for '{valid_name}'.[/bold green]")
    else:
        logger.error(f"Rollback to {prev_commit} failed.")
        console.print(f"[bold red]Rollback failed for '{valid_name}'.[/bold red]")

    return success
