"""
DeployX Health Check & Status Verification
Performs HTTP endpoint polling with retries, timeout, and intervals.
Inspects running container statuses and health state.
"""

from __future__ import annotations

import time
import urllib.request
import urllib.error
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deployx.config import paths
from deployx.core.command import CommandError
from deployx.core.security import validate_project_name
from deployx.deployment.project import load_project_config
from deployx.docker.compose import DockerComposeManager
from deployx.models import HealthStatus, ProjectConfig
from deployx.state import get_state_manager


def perform_http_healthcheck(
    port: int,
    path: str = "/",
    timeout: int = 5,
    retries: int = 6,
    interval: int = 3,
    console: Optional[Console] = None,
) -> bool:
    """
    Polls the local web application port until it responds or retries are exhausted.
    Accepts any 2xx or 3xx HTTP response, or 401/403/404 as proof the webserver is active.
    """
    clean_path = ("/" + path.lstrip("/")) if path else "/"
    url = f"http://127.0.0.1:{port}{clean_path}"

    if console:
        console.print(f"[cyan]Verifying health check at {url} (retries: {retries})...[/cyan]")

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "DeployX-HealthCheck/0.1"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                code = response.getcode()
                if 200 <= code < 400:
                    if console:
                        console.print(f"[green]  Attempt {attempt}/{retries}: Healthy (HTTP {code})[/green]")
                    return True
        except urllib.error.HTTPError as exc:
            # If server answers with 401/403/404/405, server daemon is up and responding
            if exc.code in {401, 403, 404, 405}:
                if console:
                    console.print(f"[green]  Attempt {attempt}/{retries}: Healthy (Server active, HTTP {exc.code})[/green]")
                return True
            if console:
                console.print(f"[dim]  Attempt {attempt}/{retries}: Server returned HTTP {exc.code}...[/dim]")
        except Exception as exc:
            if console:
                console.print(f"[dim]  Attempt {attempt}/{retries}: Waiting for service ({exc})...[/dim]")

        if attempt < retries:
            time.sleep(interval)

    return False


def check_project_status(project: str, console: Console) -> None:
    """
    Displays current deployment state and active container processes for a project.
    """
    valid_name = validate_project_name(project)
    cfg = load_project_config(valid_name)
    state_mgr = get_state_manager()
    state = state_mgr.get_state(valid_name)

    console.print(f"\n[bold cyan]DeployX Status: {valid_name}[/bold cyan]")

    # 1. State summary
    if not state:
        console.print("[dim]Project has no recorded deployment state yet.[/dim]")
    else:
        table = Table(show_header=False, expand=True)
        table.add_column("Key", style="bold", width=22)
        table.add_column("Value")

        status_style = "green" if state.status.value == "healthy" else "red" if state.status.value == "failed" else "yellow"
        table.add_row("Deployment Status", f"[{status_style}]{state.status.value}[/{status_style}]")
        table.add_row("Health Status", state.health_status.value)
        table.add_row("Deployed Commit", state.current_commit or "None")
        table.add_row("Docker Image", state.docker_image or "None")
        table.add_row("Last Deployed", state.deployed_at or "Never")
        if state.last_error:
            table.add_row("Last Error", f"[red]{state.last_error}[/red]")

        console.print(table)

    # 2. Container status via docker compose ps
    mgr = DockerComposeManager(valid_name)
    if mgr.compose_file.is_file():
        console.print("\n[bold]Container Status:[/bold]")
        try:
            ps_res = mgr.ps()
            console.print(ps_res.stdout or "[dim]No active containers found.[/dim]")
        except CommandError as exc:
            console.print(f"[dim]Could not query container processes: {exc.message}[/dim]")
    else:
        console.print("\n[dim]docker-compose.deployx.yml has not been generated yet.[/dim]")
