"""
DeployX CLI Application
Built with Typer and Rich for production-grade terminal ergonomics.
"""

from __future__ import annotations

import functools
import sys
from typing import List, Optional
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from pydantic import ValidationError

from deployx import __version__
from deployx.core.command import CommandError
from deployx.core.security import SecurityError, format_validation_error

console = Console()
error_console = Console(stderr=True)


def handle_cli_exceptions(func):
    """
    Decorator for Typer CLI commands to cleanly catch operational exceptions
    (PermissionError, ValidationError, SecurityError, ValueError, CommandError)
    and output human-readable rich messages without raw Python tracebacks.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except SystemExit as exc:
            if exc.code == 0:
                raise
            raise typer.Exit(exc.code if isinstance(exc.code, int) else 1)
        except PermissionError as exc:
            target_path = getattr(exc, "filename", None) or str(exc)
            if not target_path or target_path.startswith("[Errno"):
                target_path = "/opt/deployx/projects"
            cmd_invoked = None
            if len(sys.argv) > 1 and not any("pytest" in arg for arg in sys.argv):
                cmd_invoked = " ".join(sys.argv[1:])
            else:
                fn_name = func.__name__
                project_arg = kwargs.get("project") or kwargs.get("name") or (args[0] if args else "")
                if fn_name == "project_list":
                    cmd_invoked = "project list"
                elif fn_name == "project_info":
                    cmd_invoked = f"project info {project_arg}".strip()
                elif fn_name == "project_edit":
                    cmd_invoked = f"project edit {project_arg}".strip()
                elif fn_name == "project_remove":
                    cmd_invoked = f"project remove {project_arg}".strip()
                elif fn_name == "project_rename":
                    cmd_invoked = f"project rename {kwargs.get('old_name', '')} {kwargs.get('new_name', '')}".strip()
                elif fn_name == "project_add":
                    cmd_invoked = "project add"
                elif fn_name.startswith("key_"):
                    sub = fn_name.replace("key_", "")
                    cmd_invoked = f"key {sub} {project_arg}".strip()
                elif fn_name in ("deploy", "update", "status", "logs", "restart", "stop", "start", "doctor", "events", "inspect", "cleanup", "explain", "init_cmd"):
                    cmd_name = "init" if fn_name == "init_cmd" else fn_name
                    cmd_invoked = f"{cmd_name} {project_arg}".strip()
                else:
                    cmd_invoked = "<command>"

            console.print(
                f"[bold red]Permission Denied:[/bold red] DeployX does not have permission to access: {target_path}\n"
                f"Run this command with elevated privileges:\n"
                f"  [bold cyan]sudo deployx {cmd_invoked}[/bold cyan]"
            )
            raise typer.Exit(1)
        except ValidationError as exc:
            console.print(
                f"[bold red]Configuration Validation Error:[/bold red]\n{format_validation_error(exc)}"
            )
            raise typer.Exit(1)
        except SecurityError as exc:
            console.print(f"[bold red]Security Error:[/bold red] {exc}")
            raise typer.Exit(1)
        except ValueError as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            raise typer.Exit(1)
        except CommandError as exc:
            advice = f"\n[dim]{exc.actionable_advice}[/dim]" if getattr(exc, "actionable_advice", None) else ""
            console.print(f"[bold red]Command Error:[/bold red] {exc.message}{advice}")
            raise typer.Exit(1)

    return wrapper


app = typer.Typer(
    name="deployx",
    help="DeployX: Production-Grade Zero-Touch Deployment Manager for Ubuntu Linux servers.",
    no_args_is_help=True,
    add_completion=False,
)

project_app = typer.Typer(help="Manage registered projects", no_args_is_help=True)
key_app = typer.Typer(help="Manage per-project SSH deploy keys", no_args_is_help=True)
config_app = typer.Typer(help="Manage DeployX global configuration", no_args_is_help=True)
project_config_app = typer.Typer(help="Manage per-project configuration", no_args_is_help=True)
domain_app = typer.Typer(help="Manage domain routing and reverse proxy", no_args_is_help=True)

app.add_typer(project_app, name="project")
app.add_typer(key_app, name="key")
app.add_typer(config_app, name="config")
project_app.add_typer(project_config_app, name="config")
app.add_typer(domain_app, name="domain")


def version_callback(value: bool):
    if value:
        console.print(f"[bold cyan]DeployX[/bold cyan] version [bold green]{__version__}[/bold green]")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show DeployX version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
):
    """
    DeployX - Autonomous & Production-Safe Deployment Manager for Ubuntu.
    """
    pass


# ----------------------------------------------------------------------
# Init Command
# ----------------------------------------------------------------------
@app.command("init")
@handle_cli_exceptions
def init_cmd(
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Run initial server setup without prompts"),
):
    """
    Initialize DeployX on this server: verify directories, permissions, PyPI mirrors, and global configuration.
    """
    from deployx.init_wizard import run_init_wizard
    success = run_init_wizard(non_interactive=non_interactive, console=console)
    if not success:
        raise typer.Exit(1)


# ----------------------------------------------------------------------
# Doctor
# ----------------------------------------------------------------------
@app.command("doctor")
@handle_cli_exceptions
def doctor(
    docker_network: bool = typer.Option(False, "--docker-network", help="Test in-container outbound DNS and network reachability"),
    orphans: bool = typer.Option(False, "--orphans", help="Scan for orphan Docker containers, volumes, and images"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
):
    """
    Diagnose Ubuntu server environment, Docker, Git, Python, permissions, and directories.
    """
    from deployx.doctor.checks import run_doctor
    success = run_doctor(console, check_network=docker_network, orphans=orphans, json_format=json_output)
    if not success:
        raise typer.Exit(1)


# ----------------------------------------------------------------------
# Project Commands
# ----------------------------------------------------------------------
@project_app.command("add")
@handle_cli_exceptions
def project_add(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Project name"),
    git: Optional[str] = typer.Option(None, "--git", "-g", help="Git repository URL"),
    branch: str = typer.Option("main", "--branch", "-b", help="Git branch"),
    private: bool = typer.Option(False, "--private", help="Whether the repository is private"),
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Domain name"),
    framework: str = typer.Option("django", "--framework", "-f", help="Target framework"),
    database: str = typer.Option("postgres", "--database", help="Database preference (postgres/sqlite/mysql/none)"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Disable interactive prompts"),
):
    """
    Register a new project into DeployX.
    """
    from deployx.deployment.project import add_project
    add_project(
        name=name,
        git=git,
        branch=branch,
        private=private,
        domain=domain,
        framework=framework,
        database=database,
        non_interactive=non_interactive,
        console=console,
    )


@project_app.command("remove")
@handle_cli_exceptions
def project_remove(
    project: str = typer.Argument(..., help="Project name to remove"),
    purge: bool = typer.Option(False, "--purge", help="Stop and remove Docker containers, images, and networks"),
    delete_volumes: bool = typer.Option(False, "--delete-volumes", help="Permanently delete project Docker volumes (requires --purge)"),
    remove_containers: bool = typer.Option(False, "--remove-containers", help="Remove project Docker containers"),
    remove_images: bool = typer.Option(False, "--remove-images", help="Remove project Docker images"),
    remove_volumes: bool = typer.Option(False, "--remove-volumes", help="Permanently delete project Docker volumes"),
    remove_key: bool = typer.Option(False, "--remove-key", help="Remove SSH deploy key"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm destructive volume deletion in non-interactive mode"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Run without interactive confirmation prompts"),
):
    """
    Safely remove a registered project and its configuration.
    """
    from deployx.deployment.project import remove_project
    success = remove_project(
        project_name=project,
        purge=purge,
        delete_volumes=(delete_volumes or remove_volumes),
        remove_containers=remove_containers,
        remove_images=remove_images,
        remove_volumes=(remove_volumes or delete_volumes),
        remove_key=remove_key,
        force=force,
        yes=yes,
        non_interactive=non_interactive,
        console=console,
    )
    if not success:
        raise typer.Exit(1)


@project_app.command("edit")
@handle_cli_exceptions
def project_edit(
    project: str = typer.Argument(..., help="Project name to edit"),
    git: Optional[str] = typer.Option(None, "--git", "-g", help="New Git repository URL"),
    branch: Optional[str] = typer.Option(None, "--branch", "-b", help="New Git branch"),
    private: Optional[bool] = typer.Option(None, "--private", help="Mark repository as private"),
    public: Optional[bool] = typer.Option(None, "--public", help="Mark repository as public"),
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="New custom domain"),
    no_domain: bool = typer.Option(False, "--no-domain", help="Remove custom domain"),
    framework: Optional[str] = typer.Option(None, "--framework", "-f", help="New framework"),
    database: Optional[str] = typer.Option(None, "--database", help="New database preference"),
    port: Optional[int] = typer.Option(None, "--port", help="New external port"),
    redis: Optional[bool] = typer.Option(None, "--redis/--no-redis", help="Toggle Redis container"),
    worker: Optional[bool] = typer.Option(None, "--worker/--no-worker", help="Toggle Celery/RQ background worker"),
    pip_index_url: Optional[str] = typer.Option(None, "--pip-index-url", help="Custom PyPI/Python mirror index URL"),
    pip_extra_index_url: Optional[str] = typer.Option(None, "--pip-extra-index-url", help="Extra Python package index URL"),
    pip_trusted_host: Optional[str] = typer.Option(None, "--pip-trusted-host", help="Trusted host for Python package index"),
    clear_pip_index: bool = typer.Option(False, "--clear-pip-index", help="Reset Python package index to default PyPI"),
    clear_pip_extra_index: bool = typer.Option(False, "--clear-pip-extra-index", help="Reset extra package index URL"),
    clear_pip_trusted_host: bool = typer.Option(False, "--clear-pip-trusted-host", help="Reset trusted host for package index"),
    clear_all_pip_settings: bool = typer.Option(False, "--clear-all-pip-settings", help="Reset all Python package mirror settings"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Disable interactive prompts"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply changes without confirmation"),
):
    """
    Edit configuration of an existing project.
    """
    from deployx.deployment.project import edit_project
    edit_project(
        project_name=project,
        git=git,
        branch=branch,
        domain=domain,
        no_domain=no_domain,
        private=private,
        public=public,
        framework=framework,
        database=database,
        port=port,
        redis=redis,
        worker=worker,
        pip_index_url=pip_index_url,
        pip_extra_index_url=pip_extra_index_url,
        pip_trusted_host=pip_trusted_host,
        clear_pip_index=clear_pip_index,
        clear_pip_extra_index=clear_pip_extra_index,
        clear_pip_trusted_host=clear_pip_trusted_host,
        clear_all_pip_settings=clear_all_pip_settings,
        non_interactive=non_interactive,
        yes=yes,
        console=console,
    )


@project_app.command("rename")
@handle_cli_exceptions
def project_rename(
    old_name: str = typer.Argument(..., help="Current project name"),
    new_name: str = typer.Argument(..., help="New project name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm rename"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Run without interactive prompts"),
):
    """
    Safely rename an undeployed project across filesystem, configs, and keys.
    """
    from deployx.deployment.project import rename_project
    success = rename_project(
        old_name=old_name,
        new_name=new_name,
        force=force,
        yes=yes,
        non_interactive=non_interactive,
        console=console,
    )
    if not success:
        raise typer.Exit(1)


@project_app.command("list")
@handle_cli_exceptions
def project_list(
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """
    List all registered projects and their current status.
    """
    from deployx.deployment.project import list_projects
    list_projects(console, json_format=json_output)


@project_app.command("info")
@handle_cli_exceptions
def project_info(
    project: str = typer.Argument(..., help="Project name"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """
    Display configuration and deployment metadata for a project.
    """
    from deployx.deployment.project import show_project_info
    show_project_info(project, console, json_format=json_output)


@project_app.command("doctor")
@handle_cli_exceptions
def project_doctor(
    project: str = typer.Argument(..., help="Project name to diagnose"),
    network: bool = typer.Option(False, "--network", help="Test container network reachability"),
):
    """
    Run diagnostic checks on a specific project.
    """
    from deployx.doctor.checks import run_project_doctor
    success = run_project_doctor(project, check_network=network, console=console)
    if not success:
        raise typer.Exit(1)


# ----------------------------------------------------------------------
# SSH Deploy Key Commands
# ----------------------------------------------------------------------
@key_app.command("create")
@handle_cli_exceptions
def key_create(
    project: str = typer.Argument(..., help="Project name"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing key if present"),
):
    """
    Generate an isolated ED25519 SSH deploy key for a private project.
    """
    from deployx.git.ssh import create_deploy_key
    try:
        create_deploy_key(project, force=force, console=console)
    except (ValueError, SecurityError):
        raise typer.Exit(1)


@key_app.command("show")
@handle_cli_exceptions
def key_show(project: str = typer.Argument(..., help="Project name")):
    """
    Display the public SSH deploy key to add to GitHub repository settings.
    """
    from deployx.git.ssh import show_deploy_key
    try:
        show_deploy_key(project, console=console)
    except FileNotFoundError:
        raise typer.Exit(1)


@key_app.command("verify")
@handle_cli_exceptions
def key_verify(project: str = typer.Argument(..., help="Project name")):
    """
    Verify SSH deploy key access to GitHub without performing a clone.
    """
    from deployx.git.ssh import verify_deploy_key
    success = verify_deploy_key(project, console=console)
    if not success:
        raise typer.Exit(1)


@key_app.command("remove")
@handle_cli_exceptions
def key_remove(
    project: str = typer.Argument(..., help="Project name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm removal"),
):
    """
    Remove SSH deploy key for a project.
    """
    from deployx.git.ssh import remove_deploy_key
    success = remove_deploy_key(project, force=(force or yes), console=console)
    if not success:
        raise typer.Exit(1)


# ----------------------------------------------------------------------
# Lifecycle & Deployment Commands
# ----------------------------------------------------------------------
@app.command("deploy")
@handle_cli_exceptions
def deploy(
    project: str = typer.Argument(..., help="Project name or Git repository URL"),
    build_timeout: Optional[int] = typer.Option(None, "--build-timeout", help="Docker build timeout in seconds"),
    no_build_timeout: bool = typer.Option(False, "--no-build-timeout", help="Disable Docker build timeout (unlimited duration)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate deployment and preview architectural plan without modifying server"),
    explain: bool = typer.Option(False, "--explain", help="Display detector analysis rationale and architectural decisions"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm plan preview and prompts automatically"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Run without interactive confirmation prompts"),
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Custom domain for reverse proxy routing"),
    auto_rollback: Optional[bool] = typer.Option(None, "--auto-rollback/--no-auto-rollback", help="Toggle automatic rollback on deployment failure"),
    auto_branch: Optional[bool] = typer.Option(None, "--auto-branch/--no-auto-branch", help="Toggle automatic branch tracking"),
    env_secret: Optional[List[str]] = typer.Option(None, "--env-secret", help="Set environment secret in KEY=VALUE format"),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Show detailed command and build output"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output without live redraws (recommended for CI)"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable live progress bar"),
    regenerate: bool = typer.Option(False, "--regenerate", help="Force regeneration of DeployX-owned deployment files"),
):
    """
    Execute full deployment pipeline for a registered project or Git URL.
    """
    from deployx.deployment.deploy import run_deployment

    parsed_secrets: dict[str, str] = {}
    if env_secret:
        for s in env_secret:
            if "=" in s:
                k, v = s.split("=", 1)
                parsed_secrets[k.strip()] = v.strip()

    deploy_kwargs = {
        "verbose": verbose,
        "plain": (plain or no_progress),
        "regenerate": regenerate,
        "dry_run": dry_run,
        "explain": explain,
        "yes": yes,
        "non_interactive": non_interactive,
        "domain": domain,
        "auto_rollback": auto_rollback,
        "env_secrets": parsed_secrets if parsed_secrets else None,
        "console": console,
    }
    if build_timeout is not None:
        deploy_kwargs["build_timeout"] = build_timeout
    if no_build_timeout:
        deploy_kwargs["no_build_timeout"] = True
    success = run_deployment(project, **deploy_kwargs)
    if not success:
        raise typer.Exit(1)


@app.command("update")
@handle_cli_exceptions
def update(
    project: str = typer.Argument(..., help="Project name"),
    build_timeout: Optional[int] = typer.Option(None, "--build-timeout", help="Docker build timeout in seconds"),
    no_build_timeout: bool = typer.Option(False, "--no-build-timeout", help="Disable Docker build timeout (unlimited duration)"),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Show detailed command and build output"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output without live redraws (recommended for CI)"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable live progress bar"),
    regenerate: bool = typer.Option(False, "--regenerate", help="Force regeneration of DeployX-owned deployment files"),
):
    """
    Check remote Git repository for new commits and perform an incremental update.
    """
    from deployx.deployment.update import run_update
    update_kwargs = {
        "verbose": verbose,
        "plain": (plain or no_progress),
        "regenerate": regenerate,
        "console": console,
    }
    if build_timeout is not None:
        update_kwargs["build_timeout"] = build_timeout
    if no_build_timeout:
        update_kwargs["no_build_timeout"] = True
    success = run_update(project, **update_kwargs)
    if not success:
        raise typer.Exit(1)


@app.command("rollback")
@handle_cli_exceptions
def rollback(
    project: str = typer.Argument(..., help="Project name to roll back"),
    build_timeout: Optional[int] = typer.Option(None, "--build-timeout", help="Docker build timeout in seconds"),
    no_build_timeout: bool = typer.Option(False, "--no-build-timeout", help="Disable Docker build timeout (unlimited duration)"),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Show detailed command and build output"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output without live redraws"),
):
    """
    Roll back a project to its previously deployed commit.
    """
    from deployx.deployment.rollback import rollback_project
    success = rollback_project(
        project,
        build_timeout=build_timeout,
        no_build_timeout=no_build_timeout,
        verbose=verbose,
        plain=plain,
        console=console,
    )
    if not success:
        raise typer.Exit(1)


@app.command("explain")
@handle_cli_exceptions
def explain(
    project: str = typer.Argument(..., help="Project name"),
):
    """
    Display framework, Python, dependency, and infrastructure detector rationale for a project.
    """
    from deployx.config import paths
    from deployx.core.security import validate_project_name
    from deployx.detectors.registry import detect_repository

    valid_name = validate_project_name(project)
    repo_dir = paths.get_project_repo_dir(valid_name)
    if not repo_dir.is_dir():
        console.print(f"[bold red]Error:[/bold red] Repository for '{valid_name}' has not been synced yet.")
        raise typer.Exit(1)

    detection = detect_repository(repo_dir)
    tbl = Table(title=f"Detection Explanation: {valid_name}", show_header=True, header_style="bold magenta")
    tbl.add_column("Component", style="bold cyan")
    tbl.add_column("Decision")
    tbl.add_column("Rationale", style="white")

    expl = detection.explanation
    tbl.add_row("Framework", detection.framework.value, expl.get("framework_reason", "-"))
    tbl.add_row("Python Version", detection.infrastructure.selected_python_version or "3.12", expl.get("python_version_reason", "-"))
    tbl.add_row("Package Manager", detection.infrastructure.package_manager or "pip", expl.get("package_manager_reason", "-"))
    tbl.add_row("Database", expl.get("database_reason", "-"))
    tbl.add_row("Workers", expl.get("workers_reason", "-"))
    tbl.add_row("Health Endpoint", detection.infrastructure.health_endpoint or "/", expl.get("health_endpoint_reason", "-"))
    console.print(tbl)


@app.command("events")
@handle_cli_exceptions
def events(
    project: str = typer.Argument(..., help="Project name"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="Limit number of events displayed"),
    json_output: bool = typer.Option(False, "--json", help="Output timeline in JSON format"),
):
    """
    Display deployment lifecycle events and timeline for a project.
    """
    from deployx.deployment.project import show_project_timeline
    show_project_timeline(project, limit=limit, json_format=json_output, console=console)


@app.command("inspect")
@handle_cli_exceptions
def inspect(
    project: str = typer.Argument(..., help="Project name"),
    json_output: bool = typer.Option(False, "--json", help="Output inspection in JSON format"),
):
    """
    Deep inspection of project configuration, state, environment, and containers.
    """
    from deployx.deployment.project import show_project_inspect
    show_project_inspect(project, json_format=json_output, console=console)


@app.command("cleanup")
@handle_cli_exceptions
def cleanup(
    orphans: bool = typer.Option(False, "--orphans", help="Prune orphan containers and unreferenced resources"),
    volumes: bool = typer.Option(False, "--volumes", help="Prune unused and orphan Docker volumes"),
    force: bool = typer.Option(False, "--force", "-f", help="Force cleanup without interactive prompt"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm cleanup"),
):
    """
    Clean dangling Docker images, builder cache, and orphan project resources.
    """
    from deployx.cleanup import run_system_cleanup
    run_system_cleanup(orphans=orphans, delete_volumes=volumes, force=(force or yes), console=console)


@app.command("self-update")
@handle_cli_exceptions
def self_update(
    check: bool = typer.Option(False, "--check", help="Check for available updates without applying"),
    channel: str = typer.Option("stable", "--channel", help="Release channel (stable/beta)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force update even if on latest version"),
):
    """
    Check for or install updates to DeployX itself.
    """
    from deployx.self_update import SelfUpdateManager
    mgr = SelfUpdateManager(channel=channel)
    if check:
        has_update, latest_ver = mgr.check_for_update()
        if has_update:
            console.print(f"[bold green]Update available:[/bold green] {latest_ver} (current: {__version__})")
        else:
            console.print(f"[green]DeployX is up to date (version {__version__}).[/green]")
    else:
        success, msg = mgr.perform_update(force=force)
        if success:
            console.print(f"[bold green]Self-update successful:[/bold green] {msg}")
        else:
            console.print(f"[bold red]Self-update failed:[/bold red] {msg}")
            raise typer.Exit(1)


# ----------------------------------------------------------------------
# Domain Commands
# ----------------------------------------------------------------------
@domain_app.command("add")
@handle_cli_exceptions
def domain_add(
    project: str = typer.Argument(..., help="Project name"),
    domain: str = typer.Argument(..., help="Domain name (e.g. app.example.com)"),
):
    """
    Assign a domain name to a project and configure reverse proxy routing.
    """
    from deployx.deployment.project import load_project_config, save_project_config
    from deployx.network.proxy import ProxyManager
    cfg = load_project_config(project)
    cfg.deployment.domain = domain
    save_project_config(cfg)
    proxy = ProxyManager()
    proxy.add_or_update_route(project, domain, cfg.deployment.docker.port)
    console.print(f"[bold green]Configured reverse proxy route for '{project}' -> https://{domain}[/bold green]")


@domain_app.command("remove")
@handle_cli_exceptions
def domain_remove(
    project: str = typer.Argument(..., help="Project name"),
):
    """
    Remove domain name and reverse proxy routing for a project.
    """
    from deployx.deployment.project import load_project_config, save_project_config
    from deployx.network.proxy import ProxyManager
    cfg = load_project_config(project)
    old_domain = cfg.deployment.domain
    cfg.deployment.domain = None
    save_project_config(cfg)
    if old_domain:
        proxy = ProxyManager()
        proxy.remove_route(old_domain)
    console.print(f"[bold green]Removed domain route for '{project}'.[/bold green]")


# ----------------------------------------------------------------------
# Config Commands (Global & Project)
# ----------------------------------------------------------------------
@config_app.command("show")
@handle_cli_exceptions
def config_show(json_output: bool = typer.Option(False, "--json", help="Output in JSON format")):
    """
    Display current global configuration (/etc/deployx/config.yml).
    """
    import json, yaml
    from deployx.config import load_global_config
    cfg = load_global_config()
    if json_output:
        console.print(json.dumps(cfg, indent=2))
    else:
        console.print(Panel(yaml.dump(cfg, sort_keys=False), title="Global Configuration", border_style="cyan"))


@config_app.command("set")
@handle_cli_exceptions
def config_set(
    key: str = typer.Argument(..., help="Config key path (e.g. network.python.selected_mirror)"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """
    Set a value in global configuration.
    """
    from deployx.config import set_global_config_value
    success = set_global_config_value(key, value)
    if success:
        console.print(f"[bold green]Set global config '{key}' = '{value}'[/bold green]")
    else:
        console.print(f"[bold red]Failed to set global config '{key}'[/bold red]")
        raise typer.Exit(1)


@config_app.command("unset")
@handle_cli_exceptions
def config_unset(
    key: str = typer.Argument(..., help="Config key path to remove"),
):
    """
    Unset a value from global configuration.
    """
    from deployx.config import unset_global_config_value
    success = unset_global_config_value(key)
    if success:
        console.print(f"[bold green]Unset global config '{key}'[/bold green]")
    else:
        console.print(f"[bold red]Failed to unset global config '{key}'[/bold red]")
        raise typer.Exit(1)


@config_app.command("reset")
@handle_cli_exceptions
def config_reset(
    force: bool = typer.Option(False, "--force", "-f", help="Force reset without confirmation prompt"),
):
    """
    Reset global configuration to default template.
    """
    from deployx.config import reset_global_config
    success = reset_global_config()
    if success:
        console.print("[bold green]Reset global configuration to default template.[/bold green]")
    else:
        console.print("[bold red]Failed to reset global configuration.[/bold red]")
        raise typer.Exit(1)


@project_config_app.command("show")
@handle_cli_exceptions
def project_config_show(
    project: str = typer.Argument(..., help="Project name"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """
    Display configuration for a specific project.
    """
    import json, yaml
    from deployx.deployment.project import load_project_raw
    raw = load_project_raw(project)
    if json_output:
        console.print(json.dumps(raw, indent=2))
    else:
        console.print(Panel(yaml.dump(raw, sort_keys=False), title=f"Config: {project}", border_style="cyan"))


@project_config_app.command("set")
@handle_cli_exceptions
def project_config_set(
    project: str = typer.Argument(..., help="Project name"),
    key: str = typer.Argument(..., help="Key path (e.g. deployment.build_timeout)"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """
    Set a configuration property for a project.
    """
    from deployx.config import set_project_config_value
    success = set_project_config_value(project, key, value)
    if success:
        console.print(f"[bold green]Updated '{project}' config '{key}' = '{value}'[/bold green]")
    else:
        console.print(f"[bold red]Failed to update config for '{project}'[/bold red]")
        raise typer.Exit(1)


@project_config_app.command("unset")
@handle_cli_exceptions
def project_config_unset(
    project: str = typer.Argument(..., help="Project name"),
    key: str = typer.Argument(..., help="Key path to unset"),
):
    """
    Unset a configuration property for a project.
    """
    from deployx.config import unset_project_config_value
    success = unset_project_config_value(project, key)
    if success:
        console.print(f"[bold green]Unset '{project}' config '{key}'[/bold green]")
    else:
        console.print(f"[bold red]Failed to unset config for '{project}'[/bold red]")
        raise typer.Exit(1)


@app.command("generate")
@handle_cli_exceptions
def generate(
    project: str = typer.Argument(..., help="Project name"),
):
    """
    Safely regenerate DeployX-owned deployment files (Dockerfile.deployx, docker-compose.deployx.yml).
    Strictly preserves user-owned Dockerfiles and configs.
    """
    from deployx.config import paths
    from deployx.core.security import validate_project_name
    from deployx.deployment.project import load_project_config
    from deployx.detectors.registry import detect_repository
    from deployx.generators.django import is_deployx_generated_file
    from deployx.generators.registry import (
        generate_compose_for_project,
        generate_dockerfile_for_project,
    )
    from deployx.state import get_state_manager

    valid_name = validate_project_name(project)
    config = load_project_config(valid_name)
    project_dir = paths.get_project_dir(valid_name)
    repo_dir = paths.get_project_repo_dir(valid_name)
    detection = detect_repository(repo_dir)

    state = get_state_manager().get_state(valid_name)
    commit = (state.current_commit[:7]) if (state and state.current_commit) else "latest"
    image_tag = f"deployx_{valid_name}:{commit}"

    # 1. Dockerfile
    if detection.infrastructure.has_dockerfile:
        console.print(f"[dim]Preserving repository Dockerfile at {detection.infrastructure.dockerfile_path}[/dim]")
    else:
        df_path = project_dir / "Dockerfile.deployx"
        if df_path.is_file() and not is_deployx_generated_file(df_path):
            console.print(f"[yellow]Preserving user-owned Dockerfile at {df_path} (missing DeployX marker)[/yellow]")
        else:
            generate_dockerfile_for_project(config, detection, df_path)
            console.print(f"[green]Regenerated Dockerfile at {df_path}[/green]")

    # 2. Compose file
    compose_file = project_dir / "docker-compose.deployx.yml"
    if compose_file.is_file() and not is_deployx_generated_file(compose_file):
        console.print(f"[yellow]Preserving user-owned Compose file at {compose_file} (missing DeployX marker)[/yellow]")
    else:
        generate_compose_for_project(config, detection, image_tag, compose_file)
        console.print(f"[green]Regenerated Compose manifest at {compose_file}[/green]")


@app.command("status")
@handle_cli_exceptions
def status(
    project: str = typer.Argument(..., help="Project name"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """
    Check container and healthcheck status for a project.
    """
    from deployx.deployment.health import check_project_status
    check_project_status(project, console=console, json_format=json_output)


@app.command("logs")
@handle_cli_exceptions
def logs(
    project: str = typer.Argument(..., help="Project name"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    tail: int = typer.Option(100, "--tail", "-n", help="Number of lines to show"),
):
    """
    View Docker and container application logs for a project.
    """
    from deployx.docker.compose import show_project_logs
    show_project_logs(project, follow=follow, tail=tail, console=console)


@app.command("restart")
@handle_cli_exceptions
def restart(project: str = typer.Argument(..., help="Project name")):
    """
    Restart all Docker containers for a project.
    """
    from deployx.docker.compose import restart_project
    restart_project(project, console=console)


@app.command("stop")
@handle_cli_exceptions
def stop(project: str = typer.Argument(..., help="Project name")):
    """
    Stop all running Docker containers for a project.
    """
    from deployx.docker.compose import stop_project
    stop_project(project, console=console)


@app.command("start")
@handle_cli_exceptions
def start(project: str = typer.Argument(..., help="Project name")):
    """
    Start existing stopped Docker containers for a project.
    """
    from deployx.docker.compose import start_project
    start_project(project, console=console)


if __name__ == "__main__":
    app()
