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
    expected_status: int = 200,
    details: Optional[dict] = None,
    console: Optional[Console] = None,
) -> bool:
    """
    Polls the local web application port until it responds with expected status or retries are exhausted.
    Captures structured diagnostic details on error.
    """
    from deployx.core.exceptions import diagnose_health_error

    clean_path = ("/" + path.lstrip("/")) if path else "/"
    url = f"http://127.0.0.1:{port}{clean_path}"

    if console:
        console.print(f"[cyan]Verifying health check at {url} (expected: HTTP {expected_status}, retries: {retries})...[/cyan]")

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "DeployX-HealthCheck/0.1"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                code = None
                if hasattr(response, "getcode") and callable(response.getcode):
                    try:
                        ret = response.getcode()
                        if isinstance(ret, int):
                            code = ret
                    except Exception:
                        pass
                if code is None:
                    status_attr = getattr(response, "status", None)
                    if isinstance(status_attr, int):
                        code = status_attr
                if code is None:
                    code = 200

                body_sample = ""
                try:
                    raw_bytes = response.read(4096)
                    body_sample = raw_bytes.decode("utf-8", errors="replace")
                except Exception:
                    pass

                matches = (code == expected_status) or (expected_status == 200 and 200 <= code < 400)
                if matches:
                    if console:
                        console.print(f"[green]  Attempt {attempt}/{retries}: Healthy (HTTP {code})[/green]")
                    if details is not None:
                        details["last_status"] = code
                        details["status_code"] = code
                        details["response_body"] = body_sample
                        details["response_preview"] = body_sample
                        details["healthy"] = True
                    return True
                else:
                    if details is not None:
                        details["last_status"] = code
                        details["status_code"] = code
                        details["response_body"] = body_sample
                        details["response_preview"] = body_sample
                        details["diagnosis"] = f"Received HTTP {code}, expected HTTP {expected_status}."
                    if console:
                        console.print(f"[dim]  Attempt {attempt}/{retries}: HTTP {code} (expected {expected_status})...[/dim]")
        except urllib.error.HTTPError as exc:
            code = exc.code
            body_sample = ""
            try:
                raw_bytes = exc.read(4096)
                body_sample = raw_bytes.decode("utf-8", errors="replace")
            except Exception:
                pass

            diagnosis = diagnose_health_error(code, str(exc))
            if details is not None:
                details["last_status"] = code
                details["status_code"] = code
                details["last_error"] = str(exc)
                details["diagnosis"] = diagnosis
                details["response_body"] = body_sample
                details["response_preview"] = body_sample

            if code == expected_status:
                if console:
                    console.print(f"[green]  Attempt {attempt}/{retries}: Healthy (HTTP {code} matches expected)[/green]")
                return True

            if console:
                console.print(f"[dim]  Attempt {attempt}/{retries}: Server returned HTTP {code} ({diagnosis})...[/dim]")
        except Exception as exc:
            diagnosis = diagnose_health_error(None, str(exc))
            if details is not None:
                details["last_status"] = None
                details["last_error"] = str(exc)
                details["diagnosis"] = diagnosis
            if console:
                console.print(f"[dim]  Attempt {attempt}/{retries}: Waiting for service ({diagnosis})...[/dim]")

        if attempt < retries:
            time.sleep(interval)

    if details is not None and "diagnosis" not in details:
        details["diagnosis"] = "Health check timed out: no response from server."
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
