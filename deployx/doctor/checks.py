"""
DeployX Doctor Diagnostics
Inspects system readiness: Ubuntu version, Git, Docker, Compose v2,
Python, SSH client, Docker daemon, disk space, and directory permissions.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List

from rich.console import Console
from rich.table import Table

from deployx.config import paths
from deployx.core.command import CommandError, run_command


class CheckStatus(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class DoctorItem:
    category: str
    name: str
    status: CheckStatus
    details: str
    recommendation: str | None = None


def check_ubuntu_version() -> DoctorItem:
    """Checks Linux distribution and Ubuntu release version."""
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        if sys.platform == "win32":
            return DoctorItem(
                category="OS",
                name="Ubuntu Version",
                status=CheckStatus.WARNING,
                details=f"Running on Windows ({sys.platform}). Target production OS is Ubuntu 22.04 / 24.04.",
                recommendation="Deploy to Ubuntu Linux 22.04 LTS or 24.04 LTS server.",
            )
        return DoctorItem(
            category="OS",
            name="Ubuntu Version",
            status=CheckStatus.WARNING,
            details=f"Non-standard Linux system ({sys.platform}). /etc/os-release missing.",
            recommendation="DeployX is tested and supported on Ubuntu 22.04 and 24.04 LTS.",
        )

    try:
        content = os_release.read_text(encoding="utf-8")
        info = {}
        for line in content.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.strip()] = v.strip().strip('"')

        distro_id = info.get("ID", "").lower()
        version_id = info.get("VERSION_ID", "")
        pretty_name = info.get("PRETTY_NAME", f"{distro_id} {version_id}")

        if distro_id == "ubuntu":
            if version_id in {"22.04", "24.04"}:
                return DoctorItem(
                    category="OS",
                    name="Ubuntu Version",
                    status=CheckStatus.OK,
                    details=f"{pretty_name} (Supported)",
                )
            else:
                return DoctorItem(
                    category="OS",
                    name="Ubuntu Version",
                    status=CheckStatus.WARNING,
                    details=f"{pretty_name} (Expected Ubuntu 22.04 or 24.04 LTS)",
                    recommendation="DeployX is optimized for Ubuntu 22.04 and 24.04 LTS.",
                )
        else:
            return DoctorItem(
                category="OS",
                name="Ubuntu Version",
                status=CheckStatus.WARNING,
                details=f"{pretty_name} (Non-Ubuntu Linux)",
                recommendation="DeployX is officially designed for Ubuntu Linux.",
            )
    except Exception as exc:
        return DoctorItem(
            category="OS",
            name="Ubuntu Version",
            status=CheckStatus.WARNING,
            details=f"Could not read OS release: {exc}",
        )


def check_python_version() -> DoctorItem:
    """Verifies Python is version 3.12 or higher."""
    major, minor, micro = sys.version_info[:3]
    version_str = f"{major}.{minor}.{micro}"
    if (major, minor) >= (3, 12):
        return DoctorItem(
            category="Runtime",
            name="Python Version",
            status=CheckStatus.OK,
            details=f"Python {version_str} (>= 3.12)",
        )
    return DoctorItem(
        category="Runtime",
        name="Python Version",
        status=CheckStatus.ERROR,
        details=f"Python {version_str} detected",
        recommendation="Upgrade to Python 3.12 or higher: sudo apt install python3.12 python3.12-venv",
    )


def check_git() -> DoctorItem:
    """Checks Git installation and version."""
    try:
        res = run_command(["git", "--version"], timeout=10)
        return DoctorItem(
            category="Tools",
            name="Git",
            status=CheckStatus.OK,
            details=res.stdout.strip(),
        )
    except CommandError as exc:
        return DoctorItem(
            category="Tools",
            name="Git",
            status=CheckStatus.ERROR,
            details="Git is not installed or not in PATH.",
            recommendation="Install Git with: sudo apt update && sudo apt install -y git",
        )


def check_ssh_client() -> DoctorItem:
    """Checks OpenSSH client."""
    try:
        res = run_command(["ssh", "-V"], check=False, timeout=10)
        # ssh -V writes to stderr on many systems
        version_text = (res.stderr or res.stdout).strip()
        if "openssh" in version_text.lower() or res.returncode == 0:
            return DoctorItem(
                category="Tools",
                name="SSH Client",
                status=CheckStatus.OK,
                details=version_text.splitlines()[0] if version_text else "OpenSSH installed",
            )
        return DoctorItem(
            category="Tools",
            name="SSH Client",
            status=CheckStatus.WARNING,
            details="OpenSSH client returned unexpected output.",
            recommendation="Install OpenSSH with: sudo apt install -y openssh-client",
        )
    except CommandError:
        return DoctorItem(
            category="Tools",
            name="SSH Client",
            status=CheckStatus.ERROR,
            details="SSH client is not installed.",
            recommendation="Install OpenSSH with: sudo apt install -y openssh-client",
        )


def check_docker_engine() -> DoctorItem:
    """Checks Docker client installation."""
    try:
        res = run_command(["docker", "--version"], timeout=10)
        return DoctorItem(
            category="Docker",
            name="Docker CLI",
            status=CheckStatus.OK,
            details=res.stdout.strip(),
        )
    except CommandError:
        return DoctorItem(
            category="Docker",
            name="Docker CLI",
            status=CheckStatus.ERROR,
            details="Docker CLI is not installed.",
            recommendation="Install Docker Engine: curl -fsSL https://get.docker.com | sudo sh",
        )


def check_docker_compose_v2() -> DoctorItem:
    """Checks Docker Compose v2 (docker compose)."""
    try:
        res = run_command(["docker", "compose", "version"], timeout=10)
        return DoctorItem(
            category="Docker",
            name="Docker Compose v2",
            status=CheckStatus.OK,
            details=res.stdout.strip(),
        )
    except CommandError as exc:
        return DoctorItem(
            category="Docker",
            name="Docker Compose v2",
            status=CheckStatus.ERROR,
            details="Docker Compose v2 plugin is missing.",
            recommendation="Install Compose v2 plugin: sudo apt install -y docker-compose-plugin",
        )


def check_docker_daemon() -> DoctorItem:
    """Verifies Docker daemon connectivity and responsiveness."""
    try:
        res = run_command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
        server_ver = res.stdout.strip()
        return DoctorItem(
            category="Docker",
            name="Docker Daemon",
            status=CheckStatus.OK,
            details=f"Daemon active (Engine version: {server_ver})",
        )
    except CommandError as exc:
        advice = exc.actionable_advice or "Start Docker with: sudo systemctl start docker"
        return DoctorItem(
            category="Docker",
            name="Docker Daemon",
            status=CheckStatus.ERROR,
            details="Cannot connect to Docker daemon.",
            recommendation=advice,
        )


def check_disk_space() -> DoctorItem:
    """Checks available disk space on root volume."""
    try:
        check_path = paths.root_dir if paths.root_dir.exists() else Path.home()
        total, used, free = shutil.disk_usage(check_path)
        free_gb = free / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        percent_free = (free / total) * 100

        details = f"{free_gb:.1f} GB free of {total_gb:.1f} GB ({percent_free:.0f}% free)"
        if free_gb < 2.0:
            return DoctorItem(
                category="Storage",
                name="Disk Space",
                status=CheckStatus.ERROR,
                details=details,
                recommendation="Critically low disk space (< 2 GB). Clean Docker with: docker system prune -af",
            )
        elif free_gb < 5.0:
            return DoctorItem(
                category="Storage",
                name="Disk Space",
                status=CheckStatus.WARNING,
                details=details,
                recommendation="Low disk space (< 5 GB). Consider expanding disk or cleaning unused images.",
            )
        return DoctorItem(
            category="Storage",
            name="Disk Space",
            status=CheckStatus.OK,
            details=details,
        )
    except Exception as exc:
        return DoctorItem(
            category="Storage",
            name="Disk Space",
            status=CheckStatus.WARNING,
            details=f"Could not determine disk usage: {exc}",
        )


def check_deployx_directories() -> DoctorItem:
    """Checks DeployX core directories and writability."""
    try:
        paths.ensure_all_dirs()
        # Verify writable by creating and deleting a temp probe file
        probe_file = paths.root_dir / ".doctor_probe"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()

        return DoctorItem(
            category="DeployX",
            name="Directories & Permissions",
            status=CheckStatus.OK,
            details=f"Directories provisioned at {paths.root_dir} (Writable)",
        )
    except PermissionError:
        return DoctorItem(
            category="DeployX",
            name="Directories & Permissions",
            status=CheckStatus.ERROR,
            details=f"Permission denied accessing {paths.root_dir}.",
            recommendation=f"Grant ownership: sudo chown -R $USER:$USER {paths.root_dir}",
        )
    except Exception as exc:
        return DoctorItem(
            category="DeployX",
            name="Directories & Permissions",
            status=CheckStatus.ERROR,
            details=f"Error accessing directories: {exc}",
            recommendation="Run installer or fix directory permissions.",
        )


def collect_doctor_checks() -> List[DoctorItem]:
    """Runs all doctor checks and returns the list of results."""
    return [
        check_ubuntu_version(),
        check_python_version(),
        check_git(),
        check_ssh_client(),
        check_docker_engine(),
        check_docker_compose_v2(),
        check_docker_daemon(),
        check_disk_space(),
        check_deployx_directories(),
    ]


def run_doctor(console: Console) -> bool:
    """
    Executes doctor diagnostic checks and prints a formatted Rich report.
    Returns True if no ERROR checks were encountered, False otherwise.
    """
    console.print("\n[bold cyan]DeployX Doctor Diagnostic Suite[/bold cyan]")
    console.print("[dim]Scanning host environment and dependencies...[/dim]\n")

    items = collect_doctor_checks()

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Category", style="cyan", width=12)
    table.add_column("Check", style="bold", width=26)
    table.add_column("Status", width=12)
    table.add_column("Details", style="white")

    ok_count = 0
    warn_count = 0
    err_count = 0
    recommendations: List[DoctorItem] = []

    for item in items:
        if item.status == CheckStatus.OK:
            status_text = "[bold green][ OK ][/bold green]"
            ok_count += 1
        elif item.status == CheckStatus.WARNING:
            status_text = "[bold yellow][ WARN ][/bold yellow]"
            warn_count += 1
            if item.recommendation:
                recommendations.append(item)
        else:
            status_text = "[bold red][ ERROR ][/bold red]"
            err_count += 1
            if item.recommendation:
                recommendations.append(item)

        table.add_row(item.category, item.name, status_text, item.details)

    console.print(table)

    # Print recommendations if any
    if recommendations:
        console.print("\n[bold yellow]Actionable Recommendations:[/bold yellow]")
        for item in recommendations:
            badge = "[yellow]WARNING[/yellow]" if item.status == CheckStatus.WARNING else "[red]ERROR[/red]"
            console.print(f"  • {badge} [bold]{item.name}[/bold]: {item.recommendation}")

    # Summary footer
    console.print("\n[bold]Doctor Summary:[/bold] ", end="")
    console.print(f"[green]{ok_count} passed[/green], ", end="")
    if warn_count > 0:
        console.print(f"[yellow]{warn_count} warnings[/yellow], ", end="")
    if err_count > 0:
        console.print(f"[red]{err_count} errors[/red]")
    else:
        console.print("[green]0 errors[/green]")

    return err_count == 0
