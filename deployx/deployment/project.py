"""
DeployX Project Management
Handles project registration, configuration persistence (deployx.yml),
project listing, and metadata inspection.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from deployx.config import paths
from deployx.core.command import run_command
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
    DeploymentStatus,
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


def verify_public_repository(repo_url: str, branch: str) -> str:
    """
    Verifies that a public Git repository is accessible and the requested branch exists.
    Returns the resolved commit SHA on success.
    Raises ValueError on failure.
    """
    cmd = ["git", "ls-remote", repo_url, f"refs/heads/{branch}"]
    try:
        res = run_command(cmd, timeout=30, check=False)
    except Exception:
        raise ValueError("Repository not found or unreachable.")

    if not res.success:
        raise ValueError("Repository not found or unreachable.")

    lines = res.stdout.strip().splitlines()
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and (parts[1] == f"refs/heads/{branch}" or parts[1].endswith(f"/{branch}")):
            return parts[0]

    # Branch was not found directly in refs/heads/, check if repo exists at all
    cmd_head = ["git", "ls-remote", repo_url, "HEAD"]
    try:
        res_head = run_command(cmd_head, timeout=15, check=False)
        if res_head.success and res_head.stdout.strip():
            raise ValueError(f"Branch '{branch}' was not found in the repository.")
    except ValueError:
        raise
    except Exception:
        pass

    raise ValueError("Repository not found or unreachable.")


def add_project(
    name: Optional[str],
    git: Optional[str],
    branch: str = "main",
    private: bool = False,
    domain: Optional[str] = None,
    framework: str = "django",
    database: str = "postgres",
    non_interactive: bool = False,
    console: Optional[Console] = None,
) -> ProjectConfig:
    """
    Registers a new project, validates parameters, and persists deployx.yml and initial state.
    """
    if console is None:
        console = Console()

    # Interactive fallback if required parameters missing
    if non_interactive:
        if not name:
            console.print("[bold red]Error:[/bold red] --name is required.")
            raise SystemExit(1)
        if not git:
            console.print("[bold red]Error:[/bold red] --git is required.")
            raise SystemExit(1)
    else:
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

    if not non_interactive and sys.stdin.isatty() and not domain and Confirm.ask("Do you want to configure a custom domain?", default=False):
        domain = Prompt.ask("Domain name (e.g. app.example.com)")

    try:
        domain = validate_domain(domain)
    except SecurityError as exc:
        console.print(f"[bold red]Validation Error:[/bold red] {exc}")
        raise SystemExit(1)

    # Public repository remote verification
    verified = False
    if not private:
        console.print(f"[cyan]Verifying public repository access and branch '{branch}'...[/cyan]")
        try:
            verify_public_repository(git, branch)
            verified = True
            console.print("[bold green]Repository and branch verified successfully.[/bold green]")
        except ValueError as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
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
            verified=verified,
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
            f"[bold]Verified:[/bold] {'Yes' if verified else 'No'}\n"
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
    Lists all registered projects, verification state, and deployment status.
    """
    projects_dir = paths.projects_dir
    if not projects_dir.exists():
        console.print("[dim]No projects registered yet. Run 'deployx project add' to register one.[/dim]")
        return

    subdirs = [p for p in projects_dir.iterdir() if p.is_dir()]
    if not subdirs:
        console.print("[dim]No projects registered yet. Run 'deployx project add' to register one.[/dim]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", style="bold cyan")
    table.add_column("Framework")
    table.add_column("Repository", style="white")
    table.add_column("Branch")
    table.add_column("Private")
    table.add_column("Verified")
    table.add_column("Status")
    table.add_column("Commit")

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

            repo_display = cfg.git.repository
            if len(repo_display) > 36:
                repo_display = repo_display[:33] + "..."

            verified_str = "[green]Yes[/green]" if getattr(cfg.git, "verified", False) else "[yellow]No[/yellow]"

            table.add_row(
                cfg.project.name,
                cfg.deployment.framework.value,
                repo_display,
                cfg.git.branch,
                "Yes" if cfg.git.private else "No",
                verified_str,
                status_fmt,
                commit,
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
    key_status = "Present" if has_key else ("Missing" if cfg.git.private else "Not Needed")
    verified_str = "Yes" if getattr(cfg.git, "verified", False) else "No"

    info_text = [
        f"[bold]Project Name:[/bold]        {cfg.project.name}",
        f"[bold]Directory:[/bold]           {paths.get_project_dir(valid_name)}",
        f"[bold]Git Repository:[/bold]      {cfg.git.repository}",
        f"[bold]Branch:[/bold]              {cfg.git.branch}",
        f"[bold]Private Repo:[/bold]        {'Yes' if cfg.git.private else 'No'}",
        f"[bold]Repository Verified:[/bold] {verified_str}",
        f"[bold]Deploy Key:[/bold]          {key_status}",
        f"[bold]Framework:[/bold]           {cfg.deployment.framework.value}",
        f"[bold]Database:[/bold]            {cfg.deployment.database.value}",
        f"[bold]Custom Domain:[/bold]       {cfg.deployment.domain or 'None'}",
        f"[bold]Compose File:[/bold]        {cfg.deployment.docker.compose_file}",
        "",
        "[bold cyan]--- Deployment State ---[/bold cyan]",
        f"[bold]Deployment Status:[/bold]   {state.status.value if state else 'pending'}",
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


def remove_project(
    project_name: str,
    purge: bool = False,
    force: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Safely removes project metadata and state.
    If purge=True, additionally removes deploy keys and Docker resources.
    """
    if console is None:
        console = Console()

    try:
        valid_name = validate_project_name(project_name)
    except SecurityError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return False

    if not project_exists(valid_name) and not get_state_manager().get_state(valid_name):
        console.print(f"[bold red]Error:[/bold red] Project '{valid_name}' is not registered.")
        return False

    state_mgr = get_state_manager()
    state = state_mgr.get_state(valid_name)
    is_deployed = state is not None and state.status not in (DeploymentStatus.PENDING, DeploymentStatus.STOPPED)

    if not force:
        prompt_text = f"Remove project '{valid_name}'?"
        if is_deployed:
            prompt_text = f"Project '{valid_name}' is active ({state.status.value}). Remove it?"
        if purge:
            prompt_text += " (WARNING: --purge will remove all project files and deploy keys)"

        if sys.stdin.isatty():
            confirmed = Confirm.ask(prompt_text, default=False)
            if not confirmed:
                console.print("[dim]Project removal cancelled.[/dim]")
                return False
        else:
            console.print("[bold red]Error:[/bold red] Removing project in non-interactive mode requires --force.")
            return False

    if purge:
        try:
            from deployx.docker.compose import DockerComposeManager
            compose_file = paths.get_project_dir(valid_name) / "docker-compose.deployx.yml"
            if compose_file.exists():
                compose_mgr = DockerComposeManager(valid_name, compose_file=compose_file)
                compose_mgr.down(volumes=True)
        except Exception as exc:
            console.print(f"[yellow]Warning while stopping containers during purge: {exc}[/yellow]")

        paths.get_project_key_path(valid_name).unlink(missing_ok=True)
        paths.get_project_pubkey_path(valid_name).unlink(missing_ok=True)

    # Remove project directory
    pdir = paths.get_project_dir(valid_name)
    if pdir.exists():
        shutil.rmtree(pdir, ignore_errors=True)

    # Delete state
    state_mgr.delete_state(valid_name)

    # Remove logs
    log_dir = paths.get_project_log_dir(valid_name)
    if log_dir.exists():
        shutil.rmtree(log_dir, ignore_errors=True)

    console.print(f"[bold green]Project '{valid_name}' successfully removed.[/bold green]")
    return True


def edit_project(
    project_name: str,
    git: Optional[str] = None,
    branch: Optional[str] = None,
    domain: Optional[str] = None,
    framework: Optional[str] = None,
    database: Optional[str] = None,
    console: Optional[Console] = None,
) -> ProjectConfig:
    """
    Safely edits configuration fields of an existing project and validates all changes.
    """
    if console is None:
        console = Console()

    try:
        valid_name = validate_project_name(project_name)
        cfg = load_project_config(valid_name)
    except (SecurityError, FileNotFoundError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise SystemExit(1)

    changed = False

    if git:
        try:
            valid_git = validate_git_url(git)
        except SecurityError as exc:
            console.print(f"[bold red]Validation Error:[/bold red] {exc}")
            raise SystemExit(1)

        target_branch = branch or cfg.git.branch
        is_private = cfg.git.private or valid_git.startswith("git@")
        if not is_private:
            console.print(f"[cyan]Verifying updated repository '{valid_git}'...[/cyan]")
            try:
                verify_public_repository(valid_git, target_branch)
                cfg.git.verified = True
            except ValueError as exc:
                console.print(f"[bold red]Error:[/bold red] {exc}")
                raise SystemExit(1)
        else:
            cfg.git.verified = False

        cfg.git.repository = valid_git
        cfg.git.private = is_private
        changed = True

    if branch:
        branch_clean = branch.strip()
        if not branch_clean or branch_clean.startswith("-") or ".." in branch_clean:
            console.print(f"[bold red]Validation Error:[/bold red] Invalid branch name '{branch}'.")
            raise SystemExit(1)

        if not cfg.git.private:
            try:
                verify_public_repository(cfg.git.repository, branch_clean)
                cfg.git.verified = True
            except ValueError as exc:
                console.print(f"[bold red]Error:[/bold red] {exc}")
                raise SystemExit(1)

        cfg.git.branch = branch_clean
        changed = True

    if domain is not None:
        try:
            cfg.deployment.domain = validate_domain(domain)
            changed = True
        except SecurityError as exc:
            console.print(f"[bold red]Validation Error:[/bold red] {exc}")
            raise SystemExit(1)

    if framework:
        try:
            cfg.deployment.framework = FrameworkType(framework.lower())
            changed = True
        except ValueError:
            console.print(f"[bold red]Error:[/bold red] Unknown framework '{framework}'.")
            raise SystemExit(1)

    if database:
        try:
            cfg.deployment.database = DatabasePreference(database.lower())
            changed = True
        except ValueError:
            console.print(f"[bold red]Error:[/bold red] Unknown database preference '{database}'.")
            raise SystemExit(1)

    if not changed:
        console.print("[dim]No changes specified for project configuration.[/dim]")
        return cfg

    # Save updated config
    config_file = paths.get_project_config_path(valid_name)
    atomic_write_file(config_file, cfg.to_yaml(), mode=0o640)

    # Update state if git or branch changed
    state_mgr = get_state_manager()
    state = state_mgr.get_state(valid_name)
    if state:
        state.repository = cfg.git.repository
        state.branch = cfg.git.branch
        state_mgr.save_state(state)

    console.print(
        Panel(
            f"[bold green]Project '{valid_name}' configuration updated successfully![/bold green]\n\n"
            f"[bold]Repository:[/bold] {cfg.git.repository}\n"
            f"[bold]Branch:[/bold]     {cfg.git.branch}\n"
            f"[bold]Verified:[/bold]   {'Yes' if cfg.git.verified else 'No'}\n"
            f"[bold]Domain:[/bold]     {cfg.deployment.domain or 'None'}\n"
            f"[bold]Framework:[/bold]  {cfg.deployment.framework.value}\n"
            f"[bold]Database:[/bold]   {cfg.deployment.database.value}",
            title="DeployX Edit",
            border_style="green",
        )
    )
    return cfg
