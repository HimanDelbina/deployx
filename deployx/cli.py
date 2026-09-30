"""
DeployX CLI Application
Built with Typer and Rich for production-grade terminal ergonomics.
"""

from __future__ import annotations

import functools
import sys
from typing import Optional
import typer
from rich.console import Console
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
            # Format clean advice for elevated privileges
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
                elif fn_name in ("deploy", "update", "status", "logs", "restart", "stop", "start", "doctor"):
                    cmd_invoked = f"{fn_name} {project_arg}".strip()
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
    help="DeployX: Production-Grade Deployment Manager for Ubuntu Linux servers.",
    no_args_is_help=True,
    add_completion=False,
)

project_app = typer.Typer(help="Manage registered projects", no_args_is_help=True)
key_app = typer.Typer(help="Manage per-project SSH deploy keys", no_args_is_help=True)

app.add_typer(project_app, name="project")
app.add_typer(key_app, name="key")


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
# Doctor
# ----------------------------------------------------------------------
@app.command("doctor")
@handle_cli_exceptions
def doctor(
    docker_network: bool = typer.Option(False, "--docker-network", help="Test in-container outbound DNS and network reachability"),
    orphans: bool = typer.Option(False, "--orphans", help="Scan for orphan Docker containers, volumes, and images"),
):
    """
    Diagnose Ubuntu server environment, Docker, Git, Python, permissions, and directories.
    """
    from deployx.doctor.checks import run_doctor
    success = run_doctor(console, check_network=docker_network, orphans=orphans)
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
def project_list():
    """
    List all registered projects and their current status.
    """
    from deployx.deployment.project import list_projects
    list_projects(console)


@project_app.command("info")
@handle_cli_exceptions
def project_info(project: str = typer.Argument(..., help="Project name")):
    """
    Display configuration and deployment metadata for a project.
    """
    from deployx.deployment.project import show_project_info
    show_project_info(project, console)


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
    project: str = typer.Argument(..., help="Project name"),
    build_timeout: Optional[int] = typer.Option(None, "--build-timeout", help="Docker build timeout in seconds"),
    no_build_timeout: bool = typer.Option(False, "--no-build-timeout", help="Disable Docker build timeout (unlimited duration)"),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Show detailed command and build output"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output without live redraws (recommended for CI)"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable live progress bar"),
    regenerate: bool = typer.Option(False, "--regenerate", help="Force regeneration of DeployX-owned deployment files"),
):
    """
    Execute full deployment pipeline for a registered project.
    """
    from deployx.deployment.deploy import run_deployment
    deploy_kwargs = {
        "verbose": verbose,
        "plain": (plain or no_progress),
        "regenerate": regenerate,
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
    from deployx.generators.django import (
        generate_compose_file,
        generate_django_dockerfile,
        is_deployx_generated_file,
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
            generate_django_dockerfile(config, detection, df_path)
            console.print(f"[green]Regenerated Dockerfile at {df_path}[/green]")

    # 2. Compose file
    compose_file = project_dir / "docker-compose.deployx.yml"
    if compose_file.is_file() and not is_deployx_generated_file(compose_file):
        console.print(f"[yellow]Preserving user-owned Compose file at {compose_file} (missing DeployX marker)[/yellow]")
    else:
        generate_compose_file(config, detection, image_tag, compose_file)
        console.print(f"[green]Regenerated Compose manifest at {compose_file}[/green]")


@app.command("status")
@handle_cli_exceptions
def status(project: str = typer.Argument(..., help="Project name")):
    """
    Check container and healthcheck status for a project.
    """
    from deployx.deployment.health import check_project_status
    check_project_status(project, console=console)


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
