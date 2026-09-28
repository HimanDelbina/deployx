"""
DeployX Project Management
Handles project registration, configuration persistence (deployx.yml),
project listing, and metadata inspection.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from deployx.config import paths
from deployx.core.filesystem import atomic_write_file, ensure_directory
from deployx.core.security import (
    SecurityError,
    validate_domain,
    validate_git_url,
    validate_project_name,
)
from deployx.models import (
    DatabasePreference,
    DeploymentConfig,
    DeploymentState,
    DockerConfig,
    FrameworkType,
    GitConfig,
    HealthcheckConfig,
    ProjectConfig,
    ProjectMeta,
)
from deployx.state import get_state_manager


def project_exists(project_name: str) -> bool:
    """Checks whether a project configuration exists."""
    try:
        config_file = paths.get_project_config_path(project_name)
        return config_file.is_file()
    except SecurityError:
        return False


def load_project_config(project_name: str) -> ProjectConfig:
    """
    Loads and validates deployx.yml for a given project.
    Raises FileNotFoundError or ValueError if invalid.
    """
    config_file = paths.get_project_config_path(project_name)
    if not config_file.is_file():
        raise FileNotFoundError(
            f"Project '{project_name}' is not registered. (Config not found at {config_file})"
        )
    return ProjectConfig.from_yaml(config_file.read_text(encoding="utf-8"))


def add_project(
    name: Optional[str],
    git: Optional[str],
    branch: str,
    private: bool,
    domain: Optional[str],
    framework: str,
    database: str,
    console: Console,
) -> ProjectConfig:
    """
    Registers a new project, validates parameters, and persists deployx.yml and initial state.
    """
    # Interactive fallback if required parameters missing
    if not name:
        if sys.stdin.isatty():
            name = Prompt.ask("[bold cyan]Project name[/bold cyan]")
        else:
            console.print("[bold red]Error:[/bold red] --name is required.")
            raise SystemExit(1)

    try:
        name = validate_project_name(name)
    except SecurityError as exc:
        console.print(f"[bold red]Validation Error:[/bold red] {exc}")
        raise SystemExit(1)

    if project_exists(name):
        console.print(f"[bold red]Error:[/bold red] Project '{name}' already exists.")
        console.print(f"Config location: {paths.get_project_config_path(name)}")
        raise SystemExit(1)

    if not git:
        if sys.stdin.isatty():
            git = Prompt.ask("[bold cyan]Git repository URL[/bold cyan]")
        else:
            console.print("[bold red]Error:[/bold red] --git is required.")
            raise SystemExit(1)

    try:
        git = validate_git_url(git)
    except SecurityError as exc:
        console.print(f"[bold red]Validation Error:[/bold red] {exc}")
        raise SystemExit(1)

    # Auto-detect private repo if using git@... SSH syntax unless explicitly specified
    if not private and git.startswith("git@"):
        private = True

    if sys.stdin.isatty() and not domain and Confirm.ask("Do you want to configure a custom domain?", default=False):
        domain = Prompt.ask("Domain name (e.g. app.example.com)")

    try:
        domain = validate_domain(domain)
    except SecurityError as exc:
        console.print(f"[bold red]Validation Error:[/bold red] {exc}")
        raise SystemExit(1)

    # Validate framework & database enums
    try:
        fw_enum = FrameworkType(framework.lower())
    except ValueError:
        fw_enum = FrameworkType.DJANGO

    try:
        db_enum = DatabasePreference(database.lower())
    except ValueError:
        db_enum = DatabasePreference.POSTGRES

    # Build ProjectConfig model
    config = ProjectConfig(
        version=1,
        project=ProjectMeta(name=name),
        git=GitConfig(
            repository=git,
            branch=branch,
            private=private,
        ),
        deployment=DeploymentConfig(
            framework=fw_enum,
            domain=domain,
            database=db_enum,
            docker=DockerConfig(compose_file="docker-compose.deployx.yml"),
            healthcheck=HealthcheckConfig(enabled=True),
        ),
    )

    # Provision directory structure
    project_dir = paths.get_project_dir(name)
    ensure_directory(project_dir, mode=0o750)
    config_file = paths.get_project_config_path(name)

    # Write deployx.yml atomically
    atomic_write_file(config_file, config.to_yaml(), mode=0o640)

    # Initialize persistent state
    state_mgr = get_state_manager()
    initial_state = DeploymentState.new(project=name, repository=git, branch=branch)
    state_mgr.save_state(initial_state)

    console.print(
        Panel(
            f"[bold green]Project '{name}' registered successfully![/bold green]\n\n"
            f"[bold]Repository:[/bold] {git}\n"
            f"[bold]Branch:[/bold] {branch}\n"
            f"[bold]Private:[/bold] {'Yes' if private else 'No'}\n"
            f"[bold]Framework:[/bold] {fw_enum.value}\n"
            f"[bold]Database:[/bold] {db_enum.value}\n"
            f"[bold]Config Path:[/bold] {config_file}",
            title="DeployX Registration",
            border_style="green",
        )
    )

    if private:
        console.print(
            "\n[bold yellow]Next Step for Private Repository:[/bold yellow]\n"
            f"  1. Generate deploy key:   [bold cyan]deployx key create {name}[/bold cyan]\n"
            f"  2. Show public key:       [bold cyan]deployx key show {name}[/bold cyan]\n"
            "  3. Add public key to GitHub: Repo -> Settings -> Deploy keys (Allow read access)\n"
            f"  4. Verify connection:     [bold cyan]deployx key verify {name}[/bold cyan]\n"
        )
    else:
        console.print(
            f"\n[bold cyan]Next Step:[/bold cyan] Deploy the project using:\n"
            f"    [bold green]deployx deploy {name}[/bold green]\n"
        )

    return config


def list_projects(console: Console) -> None:
    """
    Lists all registered projects and their deployment state.
    """
    projects_dir = paths.projects_dir
    if not projects_dir.exists():
        console.print("[dim]No projects registered yet. Run 'deployx project add' to register one.[/dim]")
        return

    subdirs = [p for p in projects_dir.iterdir() if p.is_dir()]
    if not subdirs:
        console.print("[dim]No projects registered yet. Run 'deployx project add' to register one.[/dim]")
        return

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Project", style="bold cyan", width=18)
    table.add_column("Framework", width=12)
    table.add_column("Repository", style="white")
    table.add_column("Branch", width=10)
    table.add_column("Private", width=8)
    table.add_column("Commit", width=10)
    table.add_column("Status", width=12)

    state_mgr = get_state_manager()
    found = 0

    for pdir in sorted(subdirs):
        cfg_file = pdir / "deployx.yml"
        if not cfg_file.is_file():
            continue

        try:
            cfg = ProjectConfig.from_yaml(cfg_file.read_text(encoding="utf-8"))
            found += 1
            state = state_mgr.get_state(cfg.project.name)
            commit = (state.current_commit[:7]) if (state and state.current_commit) else "-"
            status = state.status.value if state else "registered"

            if status == "healthy":
                status_fmt = "[green]healthy[/green]"
            elif status == "failed":
                status_fmt = "[red]failed[/red]"
            elif status == "pending":
                status_fmt = "[yellow]pending[/yellow]"
            else:
                status_fmt = f"[cyan]{status}[/cyan]"

            table.add_row(
                cfg.project.name,
                cfg.deployment.framework.value,
                cfg.git.repository,
                cfg.git.branch,
                "Yes" if cfg.git.private else "No",
                commit,
                status_fmt,
            )
        except Exception:
            continue

    if found == 0:
        console.print("[dim]No projects registered yet. Run 'deployx project add' to register one.[/dim]")
        return

    console.print(table)


def show_project_info(project: str, console: Console) -> None:
    """
    Displays detailed information about a registered project.
    """
    try:
        valid_name = validate_project_name(project)
        cfg = load_project_config(valid_name)
    except (SecurityError, FileNotFoundError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise SystemExit(1)

    state_mgr = get_state_manager()
    state = state_mgr.get_state(valid_name)

    key_path = paths.get_project_key_path(valid_name)
    has_key = key_path.is_file()

    info_text = [
        f"[bold]Project Name:[/bold]        {cfg.project.name}",
        f"[bold]Directory:[/bold]           {paths.get_project_dir(valid_name)}",
        f"[bold]Git Repository:[/bold]      {cfg.git.repository}",
        f"[bold]Branch:[/bold]              {cfg.git.branch}",
        f"[bold]Private Repo:[/bold]        {'Yes' if cfg.git.private else 'No'}",
        f"[bold]SSH Deploy Key:[/bold]      {'Created' if has_key else 'Missing' if cfg.git.private else 'Not Needed'}",
        f"[bold]Framework:[/bold]           {cfg.deployment.framework.value}",
        f"[bold]Database:[/bold]            {cfg.deployment.database.value}",
        f"[bold]Custom Domain:[/bold]       {cfg.deployment.domain or 'None'}",
        f"[bold]Compose File:[/bold]        {cfg.deployment.docker.compose_file}",
        "",
        "[bold cyan]--- Deployment State ---[/bold cyan]",
        f"[bold]Status:[/bold]              {state.status.value if state else 'unknown'}",
        f"[bold]Health:[/bold]              {state.health_status.value if state else 'unknown'}",
        f"[bold]Current Commit:[/bold]      {state.current_commit or 'Not deployed yet' if state else 'None'}",
        f"[bold]Previous Commit:[/bold]     {state.previous_commit or 'None' if state else 'None'}",
        f"[bold]Last Deployed At:[/bold]    {state.deployed_at or 'Never' if state else 'Never'}",
        f"[bold]Docker Image:[/bold]        {state.docker_image or 'None' if state else 'None'}",
    ]

    if state and state.last_error:
        info_text.append(f"[bold red]Last Error:[/bold red]         {state.last_error}")

    console.print(
        Panel(
            "\n".join(info_text),
            title=f"Project Info: {valid_name}",
            border_style="cyan",
        )
    )
