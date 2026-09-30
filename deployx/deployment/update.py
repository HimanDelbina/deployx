"""
DeployX Incremental Update Manager
Inspects remote Git repository, compares deployed commit against remote HEAD,
and triggers new deployment with commit-tagged Docker images only when new changes exist.
"""

from __future__ import annotations

from typing import Optional
from rich.console import Console
from rich.panel import Panel

from deployx.core.command import CommandError
from deployx.core.security import validate_project_name
from deployx.deployment.deploy import run_deployment
from deployx.deployment.project import load_project_config
from deployx.git.repository import GitRepositoryManager
from deployx.logging.logger import ProjectLogger
from deployx.state import get_state_manager


def run_update(
    project_name: str,
    build_timeout: Optional[int] = None,
    no_build_timeout: bool = False,
    verbose: bool = False,
    plain: bool = False,
    regenerate: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Checks if a newer commit is available on the remote Git repository.
    If up to date, skips unnecessary rebuilds.
    If new commit detected, launches deployment pipeline with the new commit SHA.
    """
    valid_name = validate_project_name(project_name)
    logger = ProjectLogger(valid_name)
    try:
        config = load_project_config(valid_name)
    except Exception as exc:
        from deployx.core.security import format_validation_error
        msg = (
            f"[bold red]Project configuration for '{valid_name}' is invalid:[/bold red]\n\n"
            f"  {format_validation_error(exc)}\n\n"
            f"[bold yellow]To repair this project:[/bold yellow]\n"
            f"  [bold cyan]deployx project edit {valid_name} --git <valid_repository_url>[/bold cyan]\n\n"
            f"[bold yellow]To inspect configuration:[/bold yellow]\n"
            f"  [bold cyan]deployx project info {valid_name}[/bold cyan]"
        )
        if console:
            console.print(
                Panel(
                    msg,
                    title=f"Update Blocked: {valid_name}",
                    border_style="red",
                )
            )
        logger.error(f"Configuration validation failed for '{valid_name}': {exc}")
        return False
    state_mgr = get_state_manager()
    state = state_mgr.get_state(valid_name)

    if config.git.private and not getattr(config.git, "verified", False):
        msg = (
            "Private repository access has not been verified.\n\n"
            "Run:\n"
            f"deployx key create {valid_name}\n"
            f"deployx key show {valid_name}\n"
            f"deployx key verify {valid_name}"
        )
        if console:
            console.print(f"[bold red]{msg}[/bold red]")
        logger.error(msg)
        return False

    if console:
        console.print(f"\n[cyan]Checking for updates for project '[bold]{valid_name}[/bold]'...[/cyan]")
    logger.info(f"Checking remote repository for updates: {config.git.repository} ({config.git.branch})")

    repo_mgr = GitRepositoryManager(
        project_name=valid_name,
        repo_url=config.git.repository,
        branch=config.git.branch,
        private=config.git.private,
    )

    try:
        remote_sha = repo_mgr.get_remote_commit()
    except CommandError as exc:
        if console:
            console.print(f"[bold red]Failed to fetch remote repository commit:[/bold red]\n{exc}")
        logger.error(f"Failed to check remote commit: {exc}")
        return False

    current_sha = state.current_commit if state else None

    short_remote = remote_sha[:7]
    short_current = current_sha[:7] if current_sha else "None"

    # Check if already up to date
    if current_sha and current_sha.strip() == remote_sha.strip():
        msg = f"Project '{valid_name}' is already up to date at commit {short_current}."
        logger.info(msg)
        if console:
            console.print(
                Panel(
                    f"[bold green]Already up to date.[/bold green]\n\n"
                    f"[bold]Current Deployed Commit:[/bold] {short_current}\n"
                    f"[bold]Remote Commit:[/bold]           {short_remote}\n"
                    f"[bold]Branch:[/bold]                  {config.git.branch}",
                    title=f"Update Check: {valid_name}",
                    border_style="green",
                )
            )
        return True

    # New commit detected
    transition_msg = f"{short_current} -> {short_remote}"
    logger.info(f"New commit detected! Updating {transition_msg}")

    if console:
        console.print(
            Panel(
                f"[bold cyan]New update available![/bold cyan]\n\n"
                f"[bold]Current:[/bold]   {short_current}\n"
                f"[bold]Remote:[/bold]    {short_remote}\n"
                f"[bold]Deploying:[/bold] {transition_msg}",
                title=f"Deploying Update: {valid_name}",
                border_style="cyan",
            )
        )

    # Trigger deployment for the new commit
    deploy_kwargs = {
        "console": console,
    }
    if verbose:
        deploy_kwargs["verbose"] = True
    if plain:
        deploy_kwargs["plain"] = True
    if regenerate:
        deploy_kwargs["regenerate"] = True
    if build_timeout is not None:
        deploy_kwargs["build_timeout"] = build_timeout
    if no_build_timeout:
        deploy_kwargs["no_build_timeout"] = True
    return run_deployment(valid_name, target_commit=remote_sha, **deploy_kwargs)
