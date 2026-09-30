"""
DeployX Project Management
Handles project registration, configuration persistence (deployx.yml),
project listing, metadata inspection, configuration editing, safe project removal,
and pre-deployment project renaming.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from deployx.config import paths
from deployx.core.command import run_command
from deployx.core.filesystem import atomic_write_file, ensure_directory, set_secure_permissions
from deployx.docker.compose import DockerComposeManager, purge_project_resources
from deployx.core.security import (
    SecurityError,
    format_validation_error,
    redact_url_credentials,
    validate_domain,
    validate_git_url,
    validate_project_name,
)
from deployx.logging.logger import ProjectLogger
from deployx.models import (
    BuildConfig,
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
    PythonBuildConfig,
)
from deployx.state import get_state_manager


def project_exists(project_name: str) -> bool:
    """Checks whether a project configuration file exists on disk."""
    try:
        config_file = paths.get_project_config_path(project_name)
        return config_file.is_file()
    except SecurityError:
        return False


def load_project_raw(project_name: str) -> dict[str, Any]:
    """
    Safely loads the raw dictionary structure from deployx.yml for a given project.
    - Validates project name for path traversal
    - Checks file existence
    - Parses YAML safely
    - Verifies root structure is a dictionary
    - Does NOT perform Pydantic business/schema validation.

    Raises FileNotFoundError if project or deployx.yml does not exist.
    Raises ValueError if YAML is malformed or root is not a dictionary.
    """
    valid_name = validate_project_name(project_name)
    config_file = paths.get_project_config_path(valid_name)
    if not config_file.is_file():
        raise FileNotFoundError(
            f"Project '{valid_name}' is not registered. (Config not found at {config_file})"
        )

    try:
        content = config_file.read_text(encoding="utf-8")
        raw_data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed YAML configuration in {config_file}: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"Failed to read project config at {config_file}: {exc}") from exc

    if not isinstance(raw_data, dict):
        raise ValueError(
            f"Invalid configuration in {config_file}: Root YAML structure must be a dictionary."
        )

    return raw_data


def load_project_config(project_name: str) -> ProjectConfig:
    """
    Loads and validates deployx.yml for a given project into a strict ProjectConfig model.
    Raises FileNotFoundError, ValidationError, or ValueError if invalid.
    """
    raw_data = load_project_raw(project_name)
    return ProjectConfig.model_validate(raw_data)


def save_project_config(config: ProjectConfig) -> None:
    """
    Saves a ProjectConfig model back to its deployx.yml file atomically with secure permissions.
    """
    config_file = paths.get_project_config_path(config.project.name)
    atomic_write_file(config_file, config.to_yaml(), mode=0o640)



class ProjectConfigInspection:
    """Inspection container holding validation diagnostics and raw/validated models."""
    def __init__(
        self,
        valid: bool,
        config: Optional[ProjectConfig] = None,
        raw_data: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ):
        self.valid = valid
        self.config = config
        self.raw_data = raw_data or {}
        self.error_message = error_message


def inspect_project_config(project_name: str) -> ProjectConfigInspection:
    """
    Safely inspects a project config without raising unhandled validation exceptions.
    Returns ProjectConfigInspection with valid=True/False and detailed diagnostics.
    """
    try:
        raw_data = load_project_raw(project_name)
    except Exception as exc:
        return ProjectConfigInspection(
            valid=False,
            raw_data=None,
            error_message=str(exc),
        )

    try:
        cfg = ProjectConfig.model_validate(raw_data)
        return ProjectConfigInspection(
            valid=True,
            config=cfg,
            raw_data=raw_data,
        )
    except Exception as exc:
        return ProjectConfigInspection(
            valid=False,
            raw_data=raw_data,
            error_message=format_validation_error(exc),
        )


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
            avail = []
            try:
                res_heads = run_command(["git", "ls-remote", "--heads", repo_url], timeout=15, check=False)
                if res_heads.success and res_heads.stdout.strip():
                    for hl in res_heads.stdout.splitlines():
                        hp = hl.split()
                        if len(hp) >= 2 and hp[1].startswith("refs/heads/"):
                            avail.append(hp[1].replace("refs/heads/", ""))
            except Exception:
                pass
            msg = f"Branch '{branch}' was not found in the repository."
            if avail:
                msg += f"\nAvailable remote branches:\n" + "\n".join(f"  - {b}" for b in avail[:10])
            raise ValueError(msg)
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

    if project_exists(name) or paths.get_project_dir(name).exists():
        console.print(
            f"[bold red]Error:[/bold red] Project '{name}' already exists.\n\n"
            f"Use:\n  [bold cyan]deployx project edit {name}[/bold cyan]"
        )
        raise SystemExit(1)

    if not git:
        if sys.stdin.isatty():
            git = Prompt.ask("[bold cyan]Git repository URL[/bold cyan]")
        else:
            console.print("[bold red]Error:[/bold red] --git is required.")
            raise SystemExit(1)

    try:
        git = validate_git_url(git, check_placeholders=True)
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

    now_iso = datetime.now(timezone.utc).isoformat()

    # Build ProjectConfig model
    config = ProjectConfig(
        version=1,
        project=ProjectMeta(name=name, created_at=now_iso, updated_at=now_iso),
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

    try:
        ProjectLogger(name).info(f"Project '{name}' registered (repository: {git}, branch: {branch}, private: {private})")
    except Exception:
        pass

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


def list_projects(console: Console, json_format: bool = False) -> None:
    """
    Lists all registered projects, verification state, and deployment status.
    Never silently hides projects whose configuration contains validation errors;
    displays them with INVALID_CONFIG status so administrators can inspect or repair them.
    Supports structured JSON output via json_format.
    """
    projects_dir = paths.projects_dir
    try:
        if not projects_dir.exists():
            if json_format:
                import json
                console.print(json.dumps([]))
            else:
                console.print("[dim]No projects registered yet. Run 'deployx project add' to register one.[/dim]")
            return
        subdirs = [p for p in projects_dir.iterdir() if p.is_dir()]
    except PermissionError as exc:
        raise PermissionError(str(projects_dir)) from exc

    if not subdirs:
        if json_format:
            import json
            console.print(json.dumps([]))
        else:
            console.print("[dim]No projects registered yet. Run 'deployx project add' to register one.[/dim]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", style="bold cyan")
    table.add_column("Framework")
    table.add_column("Branch")
    table.add_column("Private")
    table.add_column("Verified")
    table.add_column("Status", no_wrap=True)
    table.add_column("Commit")
    table.add_column("Repository", style="white")

    state_mgr = get_state_manager()
    found = 0
    json_list: list[dict[str, Any]] = []

    for pdir in sorted(subdirs):
        cfg_file = pdir / "deployx.yml"
        if not cfg_file.is_file():
            continue

        found += 1
        proj_name = pdir.name
        inspection = inspect_project_config(proj_name)

        if inspection.valid and inspection.config:
            cfg = inspection.config
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
                cfg.git.branch,
                "Yes" if cfg.git.private else "No",
                verified_str,
                status_fmt,
                commit,
                repo_display,
            )
            json_list.append({
                "name": cfg.project.name,
                "framework": cfg.deployment.framework.value,
                "branch": cfg.git.branch,
                "private": cfg.git.private,
                "verified": getattr(cfg.git, "verified", False),
                "status": status,
                "commit": commit,
                "repository": cfg.git.repository,
            })
        else:
            # Broken / Invalid Project Configuration
            raw = inspection.raw_data or {}
            raw_git = raw.get("git", {}) if isinstance(raw.get("git"), dict) else {}
            raw_dep = raw.get("deployment", {}) if isinstance(raw.get("deployment"), dict) else {}
            raw_proj = raw.get("project", {}) if isinstance(raw.get("project"), dict) else {}

            name_display = raw_proj.get("name") or proj_name
            fw_display = raw_dep.get("framework") or "?"
            branch_display = raw_git.get("branch") or "?"
            private_val = raw_git.get("private")
            private_display = "Yes" if private_val is True else ("No" if private_val is False else "?")
            verified_display = "[yellow]No[/yellow]"
            status_fmt = "[bold red]INVALID_CONFIG[/bold red]"
            commit = "-"
            repo_display = raw_git.get("repository") or "[red]<invalid>[/red]"
            if len(repo_display) > 36:
                repo_display = repo_display[:33] + "..."

            table.add_row(
                str(name_display),
                str(fw_display),
                str(branch_display),
                private_display,
                verified_display,
                status_fmt,
                commit,
                repo_display,
            )
            json_list.append({
                "name": str(name_display),
                "framework": str(fw_display),
                "branch": str(branch_display),
                "private": private_val,
                "verified": False,
                "status": "INVALID_CONFIG",
                "commit": None,
                "repository": str(raw_git.get("repository") or ""),
            })

    if json_format:
        import json
        console.print(json.dumps(json_list, indent=2))
        return

    if found == 0:
        console.print("[dim]No projects registered yet. Run 'deployx project add' to register one.[/dim]")
        return

    console.print(table)


def show_project_info(project: str, console: Console, json_format: bool = False) -> None:
    """
    Displays detailed configuration, paths, and deployment metadata for a project.
    Catches and formats invalid configurations cleanly without raw tracebacks.
    """
    try:
        valid_name = validate_project_name(project)
    except SecurityError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise SystemExit(1)

    if not project_exists(valid_name) and not paths.get_project_dir(valid_name).exists():
        console.print(f"[bold red]Error:[/bold red] Project '{valid_name}' is not registered.")
        raise SystemExit(1)

    inspection = inspect_project_config(valid_name)
    if not inspection.valid:
        # Invalid project configuration - clean diagnostic display
        raw = inspection.raw_data or {}
        raw_git = raw.get("git", {}) if isinstance(raw.get("git"), dict) else {}
        repo_val = raw_git.get("repository", "Unknown / Invalid")
        config_path = paths.get_project_config_path(valid_name)

        panel_content = (
            f"[bold red]Project '{valid_name}' exists but its configuration is invalid.[/bold red]\n\n"
            f"[bold]Status:[/bold]            [bold red]INVALID_CONFIG[/bold red]\n"
            f"[bold]Config Path:[/bold]       {config_path}\n"
            f"[bold]Repository:[/bold]        {repo_val}\n\n"
            f"[bold red]Validation errors:[/bold red]\n{inspection.error_message}\n\n"
            f"[bold yellow]Actionable advice:[/bold yellow]\n"
            f"  - Edit the configuration to fix it: [bold cyan]deployx project edit {valid_name} --git <valid_repository>[/bold cyan]\n"
            f"  - Or remove the project:            [bold cyan]deployx project remove {valid_name}[/bold cyan]"
        )
        console.print(Panel(panel_content, title=f"Invalid Project Config: {valid_name}", border_style="red"))
        return

    cfg = inspection.config
    assert cfg is not None

    state_mgr = get_state_manager()
    state = state_mgr.get_state(valid_name)

    if json_format:
        import json
        info_dict = {
            "name": cfg.project.name,
            "directory": str(paths.get_project_dir(valid_name)),
            "repository": cfg.git.repository,
            "branch": cfg.git.branch,
            "private": cfg.git.private,
            "verified": getattr(cfg.git, "verified", False),
            "framework": cfg.deployment.framework.value,
            "database": cfg.deployment.database.value,
            "domain": cfg.deployment.domain,
            "status": state.status.value if state else "pending",
            "health_status": state.health_status.value if state else "unknown",
            "current_commit": state.current_commit if state else None,
            "previous_commit": state.previous_commit if state else None,
            "docker_image": state.docker_image if state else None,
            "deployed_at": state.deployed_at if state else None,
        }
        console.print(json.dumps(info_dict, indent=2))
        return

    key_path = paths.get_project_key_path(valid_name)
    has_key = key_path.is_file()
    key_status = "Present" if has_key else ("Missing" if cfg.git.private else "Not Needed")
    verified_str = "Yes" if getattr(cfg.git, "verified", False) else "No"
    created_at_val = getattr(cfg.project, "created_at", None) or (state.created_at if state else None) or "Unknown"
    updated_at_val = getattr(cfg.project, "updated_at", None) or (state.updated_at if state else None) or "Never"

    info_text = [
        f"[bold]Project Name:[/bold]        {cfg.project.name}",
        f"[bold]Directory:[/bold]           {paths.get_project_dir(valid_name)}",
        f"[bold]Git Repository:[/bold]      {cfg.git.repository}",
        f"[bold]Branch:[/bold]              {cfg.git.branch}",
        f"[bold]Private/Public:[/bold]      {'Private' if cfg.git.private else 'Public'}",
        f"[bold]Repository Verified:[/bold] {verified_str}",
        f"[bold]Deploy Key:[/bold]          {key_status}",
        f"[bold]Deploy Key Status:[/bold]   {key_status}",
        f"[bold]Framework:[/bold]           {cfg.deployment.framework.value}",
        f"[bold]Expected Database:[/bold]   {cfg.deployment.database.value}",
        f"[bold]Detected Database:[/bold]   {getattr(state, 'detected_runtime_database', None) or 'Not probed yet'}",
        f"[bold]Custom Domain:[/bold]       {cfg.deployment.domain or 'None'}",
        f"[bold]Compose File:[/bold]        {cfg.deployment.docker.compose_file}",
        f"[bold]Build Timeout:[/bold]       {str(cfg.deployment.build_timeout) + 's' if cfg.deployment.build_timeout else 'Unlimited'}",
        f"[bold]Stall Warning:[/bold]       {cfg.deployment.stall_warning_after}s",
        f"[bold]Created At:[/bold]          {created_at_val}",
        f"[bold]Updated At:[/bold]          {updated_at_val}",
    ]

    py_build = getattr(getattr(cfg, "build", None), "python", None)
    if py_build and py_build.index_url:
        info_text.append(f"[bold]Python Package Index:[/bold] Custom")
        info_text.append(f"[bold]Index URL:[/bold]            {redact_url_credentials(py_build.index_url)}")
        if py_build.extra_index_url:
            info_text.append(f"[bold]Extra Index URL:[/bold]      {redact_url_credentials(py_build.extra_index_url)}")
        if py_build.trusted_host:
            info_text.append(f"[bold]Trusted Host:[/bold]         {py_build.trusted_host}")
    else:
        info_text.append(f"[bold]Python Package Index:[/bold] Default (PyPI)")

    info_text.extend([
        "",
        "[bold cyan]--- Deployment State ---[/bold cyan]",
        f"[bold]Deployment Status:[/bold]   {state.status.value if state else 'pending'}",
        f"[bold]Health Status:[/bold]       {state.health_status.value if state else 'unknown'}",
        f"[bold]Current Commit:[/bold]      {state.current_commit or 'Not deployed yet' if state else 'Not deployed yet'}",
        f"[bold]Previous Commit:[/bold]     {state.previous_commit or 'None' if state else 'None'}",
        f"[bold]Last Deployed At:[/bold]    {state.deployed_at or 'Never' if state else 'Never'}",
        f"[bold]Docker Image:[/bold]        {state.docker_image or 'None' if state else 'None'}",
        f"[bold]Previous Image:[/bold]      {getattr(state, 'previous_image', None) or 'None'}",
        f"[bold]Last Failed Stage:[/bold]   {getattr(state, 'failed_stage', None) or 'None'}",
        f"[bold]Last Build Step:[/bold]     {getattr(state, 'last_build_step', None) or 'None'}",
        f"[bold]Last Health Check:[/bold]   {getattr(state, 'last_health_response', None) or 'None'}",
    ])

    if state and (getattr(state, "last_exception_summary", None) or state.last_error):
        err = getattr(state, "last_exception_summary", None) or state.last_error
        info_text.append(f"[bold red]Last Error:[/bold red]         {err}")

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
    delete_volumes: bool = False,
    remove_containers: bool = False,
    remove_images: bool = False,
    remove_volumes: bool = False,
    remove_key: bool = False,
    force: bool = False,
    yes: bool = False,
    non_interactive: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Safely removes project registration from DeployX.
    Works reliably even if the project's configuration file is invalid or malformed.
    Modes:
      1. Default (Safe unregister):
         Removes project metadata and state.
         Preserves Docker containers, Docker volumes, backups, and SSH deploy keys.
      2. Granular options:
         --remove-containers, --remove-images, --remove-volumes, --remove-key
      3. Purge runtime resources (--purge):
         Stops and removes Docker containers, networks, and images.
         Preserves Docker volumes, backups, and SSH deploy keys (unless specified).
      4. Destructive volume deletion (--remove-volumes / --delete-volumes):
         Permanently deletes Docker volumes.
         Requires explicit project name typing (interactive) or --yes (non-interactive).
    """
    if console is None:
        console = Console()

    try:
        valid_name = validate_project_name(project_name)
    except SecurityError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return False

    state_mgr = get_state_manager()
    state = state_mgr.get_state(valid_name)

    cfg = None
    raw_data = None
    if project_exists(valid_name) or paths.get_project_dir(valid_name).exists():
        try:
            cfg = load_project_config(valid_name)
        except Exception:
            pass

        if cfg is None:
            try:
                raw_data = load_project_raw(valid_name)
            except Exception:
                pass

    if cfg is None and raw_data is None and state is None and not paths.get_project_dir(valid_name).exists():
        console.print(f"[bold red]Error:[/bold red] Project '{valid_name}' is not registered.")
        return False

    # Resolve actions
    if delete_volumes:
        remove_volumes = True

    do_remove_containers = remove_containers or purge
    do_remove_images = remove_images or purge
    do_remove_volumes = remove_volumes
    do_remove_key = remove_key

    # Display Project Identity before confirmation (best-effort)
    repo = "Unknown"
    if cfg:
        repo = cfg.git.repository
    elif raw_data and isinstance(raw_data.get("git"), dict):
        repo = raw_data["git"].get("repository", "Invalid / Legacy")
    elif state:
        repo = state.repository

    status_val = state.status.value if state else "not deployed"

    console.print(
        f"\n[bold]Project:[/bold]           {valid_name}\n"
        f"[bold]Deployment status:[/bold] {status_val}\n"
        f"[bold]Repository:[/bold]        {repo}\n"
    )

    if cfg is None and (raw_data is not None or paths.get_project_config_path(valid_name).exists()):
        console.print("[bold yellow]Warning: Project configuration is invalid.[/bold yellow]")

    # Preview removal plan (Requirement 14)
    console.print(
        f"[bold cyan]Removal Plan:[/bold cyan]\n"
        f"  Project:    {valid_name}\n"
        f"  Containers: {'remove' if do_remove_containers else 'preserve'}\n"
        f"  Images:     {'remove' if do_remove_images else 'preserve'}\n"
        f"  Volumes:    {'remove' if do_remove_volumes else 'preserve'}\n"
        f"  Deploy key: {'remove' if do_remove_key else 'preserve'}\n"
    )

    # Confirmation depending on mode
    if do_remove_volumes:
        console.print(
            "[bold red]WARNING: DESTRUCTIVE OPERATION[/bold red]\n"
            f"This will permanently delete all Docker volumes, database data, and project files for '{valid_name}'.\n"
            "This action CANNOT be undone.\n"
        )
        if non_interactive or not sys.stdin.isatty():
            if not yes:
                console.print(
                    "[bold red]Error:[/bold red] Destructive volume deletion in non-interactive mode requires explicit --yes."
                )
                return False
        else:
            typed = Prompt.ask(
                f"Type project name '{valid_name}' to permanently delete its Docker volumes"
            )
            if typed.strip() != valid_name:
                console.print("[bold red]Confirmation failed. Project name did not match. Removal aborted.[/bold red]")
                return False

    elif do_remove_containers or do_remove_images or do_remove_key:
        console.print(
            "This will remove the project registration and clean designated runtime resources.\n"
            "Docker volumes and database data will NOT be deleted unless requested.\n"
            "Backups will NOT be deleted.\n"
        )
        if not force and not yes:
            if non_interactive or not sys.stdin.isatty():
                console.print("[bold red]Error:[/bold red] Purging project in non-interactive mode requires --force or --yes.")
                return False
            if not Confirm.ask("Continue?", default=False):
                console.print("[dim]Project removal cancelled.[/dim]")
                return False

    else:
        # Default Safe unregister
        console.print(
            "This will remove the project registration from DeployX.\n"
            "Application data and Docker volumes will NOT be deleted.\n"
        )
        if not force and not yes:
            if non_interactive or not sys.stdin.isatty():
                console.print("[bold red]Error:[/bold red] Removing project in non-interactive mode requires --force or --yes.")
                return False
            if not Confirm.ask("Continue?", default=False):
                console.print("[dim]Project removal cancelled.[/dim]")
                return False

    # Stop runtime containers via Compose if available (Requirement 14 & test compatibility)
    if do_remove_containers:
        try:
            from deployx.docker.compose import DockerComposeManager
            compose_file = paths.get_project_dir(valid_name) / "docker-compose.deployx.yml"
            if compose_file.exists():
                compose_mgr = DockerComposeManager(valid_name, compose_file=compose_file)
                compose_mgr.down(volumes=do_remove_volumes)
        except Exception as exc:
            console.print(f"[yellow]Warning while stopping containers during purge: {exc}[/yellow]")

    # Purge project resources via Docker labels/prefixes without needing compose file (Requirement 15)
    try:
        purge_project_resources(
            valid_name,
            remove_containers=do_remove_containers,
            remove_images=do_remove_images,
            remove_volumes=do_remove_volumes,
        )
    except Exception:
        pass

    # Remove SSH deploy key if requested
    key_path = paths.get_project_key_path(valid_name)
    pub_path = paths.get_project_pubkey_path(valid_name)
    key_removed = False
    if do_remove_key:
        if key_path.exists():
            key_path.unlink(missing_ok=True)
            key_removed = True
        if pub_path.exists():
            pub_path.unlink(missing_ok=True)

    # Release any open logger handles
    try:
        import logging
        old_logger = logging.getLogger(f"deployx.project.{valid_name}")
        for h in list(old_logger.handlers):
            h.close()
            old_logger.removeHandler(h)
    except Exception:
        pass

    # Remove project directory
    pdir = paths.get_project_dir(valid_name)
    if pdir.exists():
        shutil.rmtree(pdir, ignore_errors=True)

    # Delete state
    state_mgr.delete_state(valid_name)

    # Output explicit status table (Requirement 14)
    status_table = Table(title=f"Removal Status: {valid_name}", show_header=True)
    status_table.add_column("Component", style="bold")
    status_table.add_column("Status")
    status_table.add_row("Registration removed", "[bold green]YES[/bold green]")
    status_table.add_row("Containers removed", "[bold green]YES[/bold green]" if do_remove_containers else "[yellow]NO[/yellow]")
    status_table.add_row("Volumes removed", "[bold green]YES[/bold green]" if do_remove_volumes else "[yellow]NO[/yellow]")
    status_table.add_row("Images removed", "[bold green]YES[/bold green]" if do_remove_images else "[yellow]NO[/yellow]")
    status_table.add_row("Deploy key removed", "[bold green]YES[/bold green]" if key_removed else "[yellow]NO[/yellow]")
    console.print(status_table)

    # Preserved SSH deploy keys notice & actionable advice
    if key_path.exists() and not do_remove_key:
        console.print(
            f"\n[cyan]Deploy key preserved at:[/cyan]\n  {key_path}\n\n"
            f"To remove it:\n  [bold cyan]deployx key remove {valid_name}[/bold cyan]"
        )

    # Actionable command for remaining images
    if not do_remove_images:
        console.print(
            f"\n[dim]To delete remaining images:\n  docker rmi deployx_{valid_name}:current[/dim]"
        )

    # Preserved backups notice
    backup_dir = paths.backups_dir
    if backup_dir.exists():
        project_backups = list(backup_dir.glob(f"{valid_name}_*"))
        if project_backups:
            console.print(f"[dim]Project backups preserved in {backup_dir}[/dim]")

    try:
        ProjectLogger(valid_name).info(f"Project '{valid_name}' removed (purge={purge}, volumes={do_remove_volumes})")
    except Exception:
        pass

    console.print(f"[bold green]Project '{valid_name}' successfully removed.[/bold green]")
    return True


def edit_project(
    project_name: str,
    git: Optional[str] = None,
    branch: Optional[str] = None,
    domain: Optional[str] = None,
    no_domain: bool = False,
    private: Optional[bool] = None,
    public: Optional[bool] = None,
    framework: Optional[str] = None,
    database: Optional[str] = None,
    port: Optional[int] = None,
    redis: Optional[bool] = None,
    worker: Optional[bool] = None,
    pip_index_url: Optional[str] = None,
    pip_extra_index_url: Optional[str] = None,
    pip_trusted_host: Optional[str] = None,
    clear_pip_index: bool = False,
    clear_pip_extra_index: bool = False,
    clear_pip_trusted_host: bool = False,
    clear_all_pip_settings: bool = False,
    non_interactive: bool = False,
    yes: bool = False,
    console: Optional[Console] = None,
) -> ProjectConfig:
    """
    Safely edits configuration fields of an existing project and validates all changes.
    Supports repairing broken/invalid legacy configurations without requiring the old config
    to pass validation upfront.
    """
    if console is None:
        console = Console()

    try:
        valid_name = validate_project_name(project_name)
    except SecurityError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise SystemExit(1)

    if not project_exists(valid_name) and not paths.get_project_dir(valid_name).exists():
        console.print(f"[bold red]Error:[/bold red] Project '{valid_name}' is not registered.")
        raise SystemExit(1)

    # Load existing config or fallback to raw YAML for recovery
    cfg = None
    raw_data = None
    try:
        cfg = load_project_config(valid_name)
    except Exception as exc:
        try:
            raw_data = load_project_raw(valid_name)
            console.print(
                f"[bold yellow]Warning:[/bold yellow] Project '{valid_name}' configuration is currently invalid:\n"
                f"  {format_validation_error(exc)}\n"
                "[dim]Applying new configuration parameters will repair it.[/dim]\n"
            )
        except Exception as raw_exc:
            console.print(f"[bold red]Error reading project config:[/bold red] {raw_exc}")
            raise SystemExit(1)

    state_mgr = get_state_manager()
    state = state_mgr.get_state(valid_name)
    is_deployed = state is not None and (
        state.status not in (DeploymentStatus.PENDING, DeploymentStatus.STOPPED)
        or bool(state.current_commit)
    )

    if private is True and public is True:
        console.print("[bold red]Error:[/bold red] Cannot specify both --private and --public.")
        raise SystemExit(1)

    # Extract current parameters
    if cfg is not None:
        current_repo = cfg.git.repository
        current_branch = cfg.git.branch
        current_private = cfg.git.private
        current_domain = cfg.deployment.domain
        current_framework_str = cfg.deployment.framework.value
        current_database_str = cfg.deployment.database.value
        current_port = cfg.deployment.docker.port
        current_redis = cfg.deployment.redis
        current_worker = cfg.deployment.celery
        current_created_at = getattr(cfg.project, "created_at", None)
        py_build = getattr(getattr(cfg, "build", None), "python", None)
        current_pip_index = py_build.index_url if py_build else None
        current_pip_extra_index = py_build.extra_index_url if py_build else None
        current_pip_trusted_host = py_build.trusted_host if py_build else None
    else:
        raw_git = raw_data.get("git", {}) if isinstance(raw_data.get("git"), dict) else {}
        raw_dep = raw_data.get("deployment", {}) if isinstance(raw_data.get("deployment"), dict) else {}
        raw_proj = raw_data.get("project", {}) if isinstance(raw_data.get("project"), dict) else {}
        raw_docker = raw_dep.get("docker", {}) if isinstance(raw_dep.get("docker"), dict) else {}
        raw_build = raw_data.get("build", {}) if isinstance(raw_data.get("build"), dict) else {}
        raw_python = raw_build.get("python", {}) if isinstance(raw_build.get("python"), dict) else {}

        current_repo = str(raw_git.get("repository", ""))
        current_branch = str(raw_git.get("branch", "main"))
        current_private = bool(raw_git.get("private", False))
        current_domain = raw_dep.get("domain", None)
        current_framework_str = str(raw_dep.get("framework", "django"))
        current_database_str = str(raw_dep.get("database", "postgres"))
        current_port = int(raw_docker.get("port", 8000))
        current_redis = bool(raw_dep.get("redis", False))
        current_worker = bool(raw_dep.get("celery", False))
        current_created_at = raw_proj.get("created_at", None)
        current_pip_index = raw_python.get("index_url", None)
        current_pip_extra_index = raw_python.get("extra_index_url", None)
        current_pip_trusted_host = raw_python.get("trusted_host", None)

    flags_provided = (
        git is not None
        or branch is not None
        or domain is not None
        or no_domain
        or private is not None
        or public is not None
        or framework is not None
        or database is not None
        or port is not None
        or redis is not None
        or worker is not None
        or pip_index_url is not None
        or pip_extra_index_url is not None
        or pip_trusted_host is not None
        or clear_pip_index
        or clear_pip_extra_index
        or clear_pip_trusted_host
        or clear_all_pip_settings
    )

    # Interactive prompt fallback if no flags provided
    if not flags_provided:
        if non_interactive or not sys.stdin.isatty():
            console.print("[dim]No changes specified for project configuration.[/dim]")
            if cfg is not None:
                return cfg
            console.print(f"Run 'deployx project edit {valid_name} --git <url>' to fix invalid fields.")
            raise SystemExit(1)

        console.print(f"\n[bold cyan]Interactive Project Editor for '{valid_name}':[/bold cyan]")
        interactive_git = Prompt.ask("Repository URL", default=current_repo)
        interactive_branch = Prompt.ask("Branch", default=current_branch)
        interactive_private = Confirm.ask("Private repository?", default=current_private)
        interactive_domain = Prompt.ask("Custom domain (leave empty to unset)", default=current_domain or "")
        interactive_framework = Prompt.ask(
            "Framework (django/fastapi/flask/node/custom)",
            default=current_framework_str,
        )
        interactive_database = Prompt.ask(
            "Database (postgres/sqlite/mysql/none)",
            default=current_database_str,
        )

        git = interactive_git
        branch = interactive_branch
        private = interactive_private
        public = not interactive_private
        domain = interactive_domain if interactive_domain.strip() else None
        no_domain = not bool(interactive_domain.strip())
        framework = interactive_framework
        database = interactive_database

    # Resolve target candidate values
    target_private = current_private
    if private is True:
        target_private = True
    elif public is True:
        target_private = False

    target_git = current_repo
    if git is not None:
        try:
            target_git = validate_git_url(git, check_placeholders=True)
        except SecurityError as exc:
            console.print(f"[bold red]Validation Error:[/bold red] {exc}")
            raise SystemExit(1)
        if target_git.startswith("git@") and public is not True:
            target_private = True

    target_branch = current_branch
    if branch is not None:
        branch_clean = branch.strip()
        if not branch_clean or branch_clean.startswith("-") or ".." in branch_clean:
            console.print(f"[bold red]Validation Error:[/bold red] Invalid branch name '{branch}'.")
            raise SystemExit(1)
        target_branch = branch_clean

    target_domain = current_domain
    if no_domain:
        target_domain = None
    elif domain is not None:
        try:
            target_domain = validate_domain(domain)
        except SecurityError as exc:
            console.print(f"[bold red]Validation Error:[/bold red] {exc}")
            raise SystemExit(1)

    target_framework_str = current_framework_str
    if framework is not None:
        try:
            target_framework_enum = FrameworkType(framework.lower())
            target_framework_str = target_framework_enum.value
        except ValueError:
            console.print(f"[bold red]Error:[/bold red] Unknown framework '{framework}'.")
            raise SystemExit(1)

    target_database_str = current_database_str
    if database is not None:
        try:
            target_database_enum = DatabasePreference(database.lower())
            target_database_str = target_database_enum.value
        except ValueError:
            console.print(f"[bold red]Error:[/bold red] Unknown database preference '{database}'.")
            raise SystemExit(1)

    target_port = port if port is not None else current_port
    target_redis = redis if redis is not None else current_redis
    target_worker = worker if worker is not None else current_worker

    # Resolve target pip mirror parameters (Requirement 10)
    target_pip_index = current_pip_index
    target_pip_extra_index = current_pip_extra_index
    target_pip_trusted_host = current_pip_trusted_host

    if clear_all_pip_settings:
        target_pip_index = None
        target_pip_extra_index = None
        target_pip_trusted_host = None
    else:
        if clear_pip_index:
            target_pip_index = None
        elif pip_index_url is not None:
            clean_idx = pip_index_url.strip()
            target_pip_index = clean_idx if clean_idx else None

        if clear_pip_extra_index:
            target_pip_extra_index = None
        elif pip_extra_index_url is not None:
            clean_extra = pip_extra_index_url.strip()
            target_pip_extra_index = clean_extra if clean_extra else None

        if clear_pip_trusted_host:
            target_pip_trusted_host = None
        elif pip_trusted_host is not None:
            clean_host = pip_trusted_host.strip()
            target_pip_trusted_host = clean_host if clean_host else None

    # Build and validate proposed new ProjectConfig
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        proposed_cfg = ProjectConfig(
            version=1,
            project=ProjectMeta(
                name=valid_name,
                created_at=current_created_at or now_iso,
                updated_at=now_iso,
            ),
            git=GitConfig(
                repository=target_git,
                branch=target_branch,
                private=target_private,
                verified=False,
            ),
            deployment=DeploymentConfig(
                framework=FrameworkType(target_framework_str),
                domain=target_domain,
                database=DatabasePreference(target_database_str),
                docker=DockerConfig(compose_file="docker-compose.deployx.yml", port=target_port),
                healthcheck=HealthcheckConfig(enabled=True),
                redis=target_redis,
                celery=target_worker,
            ),
            build=BuildConfig(
                python=PythonBuildConfig(
                    index_url=target_pip_index,
                    extra_index_url=target_pip_extra_index,
                    trusted_host=target_pip_trusted_host,
                )
            ),
        )
    except Exception as exc:
        console.print(
            f"[bold red]Configuration Validation Error:[/bold red]\n{format_validation_error(exc)}"
        )
        raise SystemExit(1)

    # Display Current vs New Summary Panel
    curr_pip_display = redact_url_credentials(current_pip_index) if current_pip_index else "Default (PyPI)"
    target_pip_display = redact_url_credentials(target_pip_index) if target_pip_index else "Default (PyPI)"

    summary_text = (
        f"[bold]Current configuration:[/bold]\n"
        f"  Repository: {current_repo}\n"
        f"  Branch:     {current_branch}\n"
        f"  Private:    {'Yes' if current_private else 'No'}\n"
        f"  Domain:     {current_domain or 'None'}\n"
        f"  Framework:  {current_framework_str}\n"
        f"  Database:   {current_database_str}\n"
        f"  PIP Index:  {curr_pip_display}\n\n"
        f"[bold]New configuration:[/bold]\n"
        f"  Repository: {target_git}\n"
        f"  Branch:     {target_branch}\n"
        f"  Private:    {'Yes' if target_private else 'No'}\n"
        f"  Domain:     {target_domain or 'None'}\n"
        f"  Framework:  {target_framework_str}\n"
        f"  Database:   {target_database_str}\n"
        f"  PIP Index:  {target_pip_display}"
    )
    console.print(Panel(summary_text, title=f"Project Configuration Edit: {valid_name}", border_style="cyan"))

    if is_deployed:
        console.print(
            "\n[bold yellow]Warning:[/bold yellow] This project has already been deployed.\n"
            "Configuration changes may require a redeployment.\n"
        )

    # Prompt confirmation unless yes or non_interactive
    if not yes and not non_interactive:
        confirmed = False
        try:
            confirmed = Confirm.ask("Apply changes?", default=False)
        except Exception:
            confirmed = False
        if not confirmed:
            console.print("[dim]Edit cancelled. No changes applied.[/dim]")
            if cfg is not None:
                return cfg
            raise SystemExit(0)

    # Verification invalidation and transition handling
    repo_changed = (
        target_git != current_repo
        or target_branch != current_branch
        or target_private != current_private
        or cfg is None  # Recovered from invalid config
    )

    if repo_changed:
        if target_private:
            target_verified = False
            console.print(
                "\n[bold yellow]Repository configuration changed.[/bold yellow]\n"
                "Previous repository verification is no longer valid.\n\n"
                f"Run:\n  [bold cyan]deployx key verify {valid_name}[/bold cyan]"
            )
            key_path = paths.get_project_key_path(valid_name)
            if not key_path.exists():
                console.print(
                    "\n[bold yellow]Next steps for private repository:[/bold yellow]\n"
                    f"  1. Generate deploy key:   [bold cyan]deployx key create {valid_name}[/bold cyan]\n"
                    f"  2. Show public key:       [bold cyan]deployx key show {valid_name}[/bold cyan]\n"
                    "  3. Add to GitHub:         Settings -> Deploy keys\n"
                    f"  4. Verify connection:     [bold cyan]deployx key verify {valid_name}[/bold cyan]"
                )
        else:
            # Transition to or remaining Public
            if current_private and not target_private:
                key_path = paths.get_project_key_path(valid_name)
                if key_path.exists():
                    console.print(
                        f"\n[cyan]Notice:[/cyan] Existing deploy key preserved at {key_path} (no longer required for public repository)."
                    )
            console.print(f"[cyan]Verifying public repository '{target_git}' (branch: '{target_branch}')...[/cyan]")
            try:
                verify_public_repository(target_git, target_branch)
                target_verified = True
                console.print("[bold green]Public repository and branch verified successfully.[/bold green]")
            except ValueError as exc:
                console.print(f"[bold red]Public repository verification failed:[/bold red] {exc}")
                raise SystemExit(1)
    else:
        target_verified = getattr(cfg.git, "verified", False) if cfg else False

    proposed_cfg.git.verified = target_verified

    # Atomic write to deployx.yml with backup (Requirement 11)
    config_file = paths.get_project_config_path(valid_name)
    try:
        from deployx.config import backup_project_file
        backup_project_file(config_file, max_backups=5)
    except Exception:
        pass
    atomic_write_file(config_file, proposed_cfg.to_yaml(), mode=0o640)

    # Update state
    if state:
        state.repository = target_git
        state.branch = target_branch
        state.updated_at = now_iso
        state_mgr.save_state(state)

    try:
        ProjectLogger(valid_name).info(
            f"Project configuration edited: repo={target_git}, branch={target_branch}, private={target_private}, verified={target_verified}"
        )
    except Exception:
        pass

    console.print(f"[bold green]Project '{valid_name}' configuration updated successfully![/bold green]")

    if is_deployed:
        console.print(
            f"\n[bold cyan]Next Step:[/bold cyan] Apply changes to containers using:\n"
            f"    [bold green]deployx deploy {valid_name}[/bold green]\n"
            "  or\n"
            f"    [bold green]deployx update {valid_name}[/bold green]"
        )

    return proposed_cfg


def rename_project(
    old_name: str,
    new_name: str,
    force: bool = False,
    yes: bool = False,
    non_interactive: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Safely renames an undeployed project across filesystem, configuration, state, and deploy keys.
    Strictly forbids renaming already-deployed projects to prevent container/volume drift.
    """
    if console is None:
        console = Console()

    try:
        valid_old = validate_project_name(old_name)
    except SecurityError as exc:
        console.print(f"[bold red]Error in old project name:[/bold red] {exc}")
        return False

    try:
        valid_new = validate_project_name(new_name)
    except SecurityError as exc:
        console.print(f"[bold red]Error in new project name:[/bold red] {exc}")
        return False

    if valid_old == valid_new:
        console.print("[bold red]Error:[/bold red] Old name and new name cannot be identical.")
        return False

    state_mgr = get_state_manager()
    old_state = state_mgr.get_state(valid_old)
    if not project_exists(valid_old) and old_state is None and not paths.get_project_dir(valid_old).exists():
        console.print(f"[bold red]Error:[/bold red] Project '{valid_old}' is not registered.")
        return False

    if project_exists(valid_new) or state_mgr.get_state(valid_new) is not None or paths.get_project_dir(valid_new).exists():
        console.print(f"[bold red]Error:[/bold red] Target project '{valid_new}' already exists.")
        return False

    # Check if project has already been deployed
    if old_state is not None and (
        old_state.status not in (DeploymentStatus.PENDING, DeploymentStatus.STOPPED)
        or bool(old_state.current_commit)
    ):
        console.print("[bold red]Error:[/bold red] Project rename is currently supported only before first deployment.")
        return False

    cfg = None
    raw = None
    try:
        cfg = load_project_config(valid_old)
    except Exception:
        try:
            raw = load_project_raw(valid_old)
        except Exception:
            raw = None

    if cfg:
        repo_display = cfg.git.repository
        branch_display = cfg.git.branch
    elif raw and isinstance(raw.get("git"), dict):
        repo_display = raw["git"].get("repository", "Unknown")
        branch_display = raw["git"].get("branch", "main")
    else:
        repo_display = old_state.repository if old_state else "Unknown"
        branch_display = old_state.branch if old_state else "main"

    console.print(
        f"\n[bold]Rename project:[/bold]\n"
        f"  Current name: {valid_old}\n"
        f"  New name:     {valid_new}\n"
        f"  Repository:   {repo_display}\n"
        f"  Branch:       {branch_display}\n"
    )

    if not force and not yes and not non_interactive:
        if sys.stdin.isatty():
            if not Confirm.ask(f"Rename project '{valid_old}' to '{valid_new}'?", default=False):
                console.print("[dim]Project rename cancelled.[/dim]")
                return False
        else:
            console.print("[bold red]Error:[/bold red] Renaming project in non-interactive mode requires --force or --yes.")
            return False

    # Paths
    old_pdir = paths.get_project_dir(valid_old)
    new_pdir = paths.get_project_dir(valid_new)
    old_key = paths.get_project_key_path(valid_old)
    new_key = paths.get_project_key_path(valid_new)
    old_pub = paths.get_project_pubkey_path(valid_old)
    new_pub = paths.get_project_pubkey_path(valid_new)
    old_log = paths.get_project_log_dir(valid_old)
    new_log = paths.get_project_log_dir(valid_new)

    actions_done: list[str] = []

    try:
        # 1. Rename project dir
        if old_pdir.exists():
            shutil.move(str(old_pdir), str(new_pdir))
            actions_done.append("move_pdir")

        # 2. Update config file in new_pdir
        new_cfg_file = paths.get_project_config_path(valid_new)
        if cfg:
            cfg.project.name = valid_new
            cfg.project.updated_at = datetime.now(timezone.utc).isoformat()
            atomic_write_file(new_cfg_file, cfg.to_yaml(), mode=0o640)
            actions_done.append("update_cfg")
        elif raw:
            if isinstance(raw.get("project"), dict):
                raw["project"]["name"] = valid_new
                raw["project"]["updated_at"] = datetime.now(timezone.utc).isoformat()
            atomic_write_file(new_cfg_file, yaml.dump(raw, sort_keys=False), mode=0o640)
            actions_done.append("update_cfg")

        # 3. Rename state file
        if old_state:
            old_state.project = valid_new
            old_state.updated_at = datetime.now(timezone.utc).isoformat()
            state_mgr.save_state(old_state)
            state_mgr.delete_state(valid_old)
            actions_done.append("update_state")

        # 4. Migrate deploy keys if present
        if old_key.exists():
            shutil.move(str(old_key), str(new_key))
            set_secure_permissions(new_key, mode=0o600)
            actions_done.append("move_key")
        if old_pub.exists():
            shutil.move(str(old_pub), str(new_pub))
            set_secure_permissions(new_pub, mode=0o644)
            actions_done.append("move_pub")

        # 5. Migrate logs if present
        if old_log.exists():
            try:
                import logging
                old_logger = logging.getLogger(f"deployx.project.{valid_old}")
                for h in list(old_logger.handlers):
                    h.close()
                    old_logger.removeHandler(h)
            except Exception:
                pass
            shutil.move(str(old_log), str(new_log))
            actions_done.append("move_log")

    except Exception as exc:
        # Atomic rollback
        if "move_log" in actions_done and new_log.exists():
            shutil.move(str(new_log), str(old_log))
        if "move_pub" in actions_done and new_pub.exists():
            shutil.move(str(new_pub), str(old_pub))
        if "move_key" in actions_done and new_key.exists():
            shutil.move(str(new_key), str(old_key))
        if "update_state" in actions_done and old_state:
            old_state.project = valid_old
            state_mgr.save_state(old_state)
            state_mgr.delete_state(valid_new)
        if "move_pdir" in actions_done and new_pdir.exists():
            shutil.move(str(new_pdir), str(old_pdir))
        console.print(f"[bold red]Failed to rename project:[/bold red] {exc}")
        return False

    try:
        ProjectLogger(valid_new).info(f"Project renamed from '{valid_old}' to '{valid_new}'")
    except Exception:
        pass

    console.print(f"[bold green]Project '{valid_old}' successfully renamed to '{valid_new}'.[/bold green]")
    return True


def get_project_timeline(project_name: str) -> list[dict[str, Any]]:
    """Retrieves deployment timeline events for a project."""
    valid_name = validate_project_name(project_name)
    state = get_state_manager().get_state(valid_name)
    return state.timeline if state else []


def show_project_timeline(
    project_name: str,
    limit: Optional[int] = None,
    json_format: bool = False,
    console: Optional[Console] = None,
) -> None:
    """Renders formatted or JSON deployment timeline events."""
    if console is None:
        console = Console()
    valid_name = validate_project_name(project_name)
    events = get_project_timeline(valid_name)
    if limit and limit > 0:
        events = events[-limit:]

    if json_format:
        import json
        console.print(json.dumps(events, indent=2))
        return

    if not events:
        console.print(f"[dim]No timeline events recorded for project '{valid_name}'.[/dim]")
        return

    table = Table(title=f"Deployment Timeline: {valid_name}", show_header=True, header_style="bold magenta")
    table.add_column("Timestamp", style="dim", width=20)
    table.add_column("Stage", style="bold cyan", width=16)
    table.add_column("Status", width=12)
    table.add_column("Message", style="white")

    for ev in events:
        st = ev.get("status", "unknown")
        if st in ("success", "healthy", "passed"):
            st_fmt = "[green]SUCCESS[/green]"
        elif st in ("failed", "error"):
            st_fmt = "[red]FAILED[/red]"
        elif st in ("started", "running"):
            st_fmt = "[yellow]STARTED[/yellow]"
        elif st in ("retry", "retrying"):
            st_fmt = "[yellow]RETRY[/yellow]"
        else:
            st_fmt = f"[cyan]{st.upper()}[/cyan]"

        table.add_row(
            ev.get("timestamp", "")[:19],
            ev.get("stage", ""),
            st_fmt,
            ev.get("message", ""),
        )

    console.print(table)


def inspect_project_details(project_name: str) -> dict[str, Any]:
    """Collects comprehensive deep inspection dictionary for a project."""
    valid_name = validate_project_name(project_name)
    inspection = inspect_project_config(valid_name)
    state = get_state_manager().get_state(valid_name)

    res: dict[str, Any] = {
        "project": valid_name,
        "valid_config": inspection.valid,
        "config": inspection.config.model_dump(mode="json") if inspection.config else inspection.raw_data,
        "state": state.model_dump(mode="json") if state else None,
    }

    if inspection.config and inspection.config.deployment:
        res["env_secrets_keys"] = list(inspection.config.deployment.env_secrets.keys())

    from deployx.docker.compose import find_managed_containers
    try:
        res["containers"] = [c for c in find_managed_containers() if valid_name in c.get("name", "")]
    except Exception:
        res["containers"] = []
    return res


def show_project_inspect(
    project_name: str,
    json_format: bool = False,
    console: Optional[Console] = None,
) -> None:
    """Displays deep project inspection details in Rich panels or JSON."""
    if console is None:
        console = Console()
    details = inspect_project_details(project_name)

    if json_format:
        import json
        console.print(json.dumps(details, indent=2))
        return

    console.print(f"\n[bold cyan]Deep Inspection for '{project_name}'[/bold cyan]\n")
    st = details.get("state") or {}

    table = Table(title="Project Overview", show_header=False)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")
    table.add_row("Project Name", details.get("project", ""))
    table.add_row("Configuration Valid", "[green]Yes[/green]" if details.get("valid_config") else "[red]No[/red]")
    if st:
        table.add_row("Deployment Status", st.get("status", "unknown"))
        table.add_row("Current Commit", st.get("current_commit") or "None")
        table.add_row("Docker Image", st.get("docker_image") or "None")
        table.add_row("Health Status", st.get("health_status", "unknown"))
        table.add_row("Detected Framework", st.get("detected_framework") or "unknown")
        table.add_row("Detected Python", st.get("detected_python_version") or "unknown")
        table.add_row("Detected Package Mgr", st.get("detected_package_manager") or "unknown")
        table.add_row("Timeline Events", str(len(st.get("timeline", []))))
    console.print(table)

    secrets = details.get("env_secrets_keys", [])
    if secrets:
        console.print(f"\n[bold]Configured Secrets (keys only):[/bold] {', '.join(secrets)}")

    containers = details.get("containers", [])
    if containers:
        ctable = Table(title="Managed Containers", show_header=True, header_style="bold magenta")
        ctable.add_column("ID", style="dim")
        ctable.add_column("Name", style="bold")
        ctable.add_column("Status")
        ctable.add_column("Ports")
        for c in containers:
            ctable.add_row(c.get("id", "")[:12], c.get("name", ""), c.get("status", ""), c.get("ports", ""))
        console.print(ctable)

