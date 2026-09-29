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
def doctor():
    """
    Diagnose Ubuntu server environment, Docker, Git, Python, permissions, and directories.
    """
    from deployx.doctor.checks import run_doctor
    run_doctor(console)


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
    purge: bool = typer.Option(False, "--purge", help="Stop and remove Docker containers and networks"),
    delete_volumes: bool = typer.Option(False, "--delete-volumes", help="Permanently delete project Docker volumes (requires --purge)"),
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
        delete_volumes=delete_volumes,
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
def deploy(project: str = typer.Argument(..., help="Project name")):
    """
    Execute full deployment pipeline for a registered project.
    """
    from deployx.deployment.deploy import run_deployment
    success = run_deployment(project, console=console)
    if not success:
        raise typer.Exit(1)


@app.command("update")
@handle_cli_exceptions
def update(project: str = typer.Argument(..., help="Project name")):
    """
    Check remote Git repository for new commits and perform an incremental update.
    """
    from deployx.deployment.update import run_update
    success = run_update(project, console=console)
    if not success:
        raise typer.Exit(1)


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
