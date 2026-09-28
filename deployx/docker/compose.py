"""
DeployX Docker Compose v2 Manager
Strictly uses Docker Compose v2 (`docker compose`, not legacy `docker-compose`).
Provides process-safe execution of container lifecycle commands.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from deployx.config import paths
from deployx.core.command import CommandError, CommandResult, run_command
from deployx.core.security import validate_project_name


class DockerComposeManager:
    """Manages Docker Compose v2 lifecycle operations for a specific project."""

    def __init__(self, project_name: str, compose_file: Optional[Path] = None):
        self.project_name = validate_project_name(project_name)
        self.project_dir = paths.get_project_dir(self.project_name)
        self.compose_file = compose_file or (self.project_dir / "docker-compose.deployx.yml")

    def _base_cmd(self) -> List[str]:
        return ["docker", "compose", "-f", str(self.compose_file)]

    def build(self, service: Optional[str] = None, no_cache: bool = False) -> CommandResult:
        """Runs 'docker compose build'."""
        cmd = self._base_cmd() + ["build"]
        if no_cache:
            cmd.append("--no-cache")
        if service:
            cmd.append(service)
        return run_command(cmd, cwd=self.project_dir, timeout=600)

    def up(self, services: Optional[List[str]] = None, detach: bool = True) -> CommandResult:
        """Runs 'docker compose up -d'."""
        cmd = self._base_cmd() + ["up"]
        if detach:
            cmd.append("-d")
        if services:
            cmd.extend(services)
        return run_command(cmd, cwd=self.project_dir, timeout=180)

    def down(self, remove_volumes: bool = False) -> CommandResult:
        """Runs 'docker compose down'."""
        cmd = self._base_cmd() + ["down"]
        if remove_volumes:
            cmd.append("-v")
        return run_command(cmd, cwd=self.project_dir, timeout=120)

    def stop(self, services: Optional[List[str]] = None) -> CommandResult:
        """Runs 'docker compose stop'."""
        cmd = self._base_cmd() + ["stop"]
        if services:
            cmd.extend(services)
        return run_command(cmd, cwd=self.project_dir, timeout=60)

    def start(self, services: Optional[List[str]] = None) -> CommandResult:
        """Runs 'docker compose start'."""
        cmd = self._base_cmd() + ["start"]
        if services:
            cmd.extend(services)
        return run_command(cmd, cwd=self.project_dir, timeout=60)

    def restart(self, services: Optional[List[str]] = None) -> CommandResult:
        """Runs 'docker compose restart'."""
        cmd = self._base_cmd() + ["restart"]
        if services:
            cmd.extend(services)
        return run_command(cmd, cwd=self.project_dir, timeout=60)

    def run_transient(
        self,
        service: str,
        command: List[str],
        timeout: int = 300,
    ) -> CommandResult:
        """
        Runs a one-off command in a service container without starting the service daemon.
        Uses 'docker compose run --rm -T <service> <command>'.
        """
        cmd = self._base_cmd() + ["run", "--rm", "-T", service] + command
        return run_command(cmd, cwd=self.project_dir, timeout=timeout)

    def ps(self) -> CommandResult:
        """Runs 'docker compose ps'."""
        cmd = self._base_cmd() + ["ps", "--format", "table {{.Name}}\t{{.Status}}\t{{.Ports}}"]
        return run_command(cmd, cwd=self.project_dir, timeout=30)

    def logs(self, service: Optional[str] = None, tail: int = 100) -> CommandResult:
        """Runs 'docker compose logs'."""
        cmd = self._base_cmd() + ["logs", f"--tail={tail}"]
        if service:
            cmd.append(service)
        return run_command(cmd, cwd=self.project_dir, timeout=30)


# CLI command handlers
def restart_project(project: str, console: Console) -> None:
    valid_name = validate_project_name(project)
    mgr = DockerComposeManager(valid_name)
    console.print(f"[cyan]Restarting containers for project '{valid_name}'...[/cyan]")
    try:
        mgr.restart()
        console.print(f"[bold green]Containers restarted successfully for '{valid_name}'.[/bold green]")
    except CommandError as exc:
        console.print(f"[bold red]Failed to restart containers:[/bold red] {exc}")
        raise SystemExit(1)


def stop_project(project: str, console: Console) -> None:
    valid_name = validate_project_name(project)
    mgr = DockerComposeManager(valid_name)
    console.print(f"[yellow]Stopping containers for project '{valid_name}'...[/yellow]")
    try:
        mgr.stop()
        console.print(f"[bold green]Containers stopped for '{valid_name}'.[/bold green]")
    except CommandError as exc:
        console.print(f"[bold red]Failed to stop containers:[/bold red] {exc}")
        raise SystemExit(1)


def start_project(project: str, console: Console) -> None:
    valid_name = validate_project_name(project)
    mgr = DockerComposeManager(valid_name)
    console.print(f"[cyan]Starting containers for project '{valid_name}'...[/cyan]")
    try:
        mgr.start()
        console.print(f"[bold green]Containers started for '{valid_name}'.[/bold green]")
    except CommandError as exc:
        console.print(f"[bold red]Failed to start containers:[/bold red] {exc}")
        raise SystemExit(1)


def show_project_logs(
    project: str,
    follow: bool = False,
    tail: int = 100,
    console: Optional[Console] = None,
) -> None:
    valid_name = validate_project_name(project)
    mgr = DockerComposeManager(valid_name)
    if not mgr.compose_file.is_file():
        if console:
            console.print(f"[bold red]Error:[/bold red] Project '{valid_name}' has not been deployed yet.")
        raise SystemExit(1)

    try:
        res = mgr.logs(tail=tail)
        if console:
            console.print(res.stdout or "[dim]No logs recorded yet.[/dim]")
    except CommandError as exc:
        if console:
            console.print(f"[bold red]Failed to fetch logs:[/bold red] {exc}")
        raise SystemExit(1)
