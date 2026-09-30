"""
DeployX Doctor Diagnostics
Inspects system readiness: Ubuntu version, Git, Docker, Compose v2,
Python, SSH client, Docker daemon, disk space, and directory permissions.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import socket
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List
from urllib.parse import urlparse

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
                    details=f"Ubuntu {version_id} detected (not yet in validated support matrix)",
                    recommendation=f"DeployX has not yet been formally validated on Ubuntu {version_id}. Continuing in compatibility mode (validated: 22.04, 24.04 LTS).",
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
    """Verifies Python is version 3.10 or higher."""
    major, minor, micro = sys.version_info[:3]
    version_str = f"{major}.{minor}.{micro}"
    if (major, minor) >= (3, 10):
        return DoctorItem(
            category="Runtime",
            name="Python Version",
            status=CheckStatus.OK,
            details=f"Python {version_str} (>= 3.10)",
        )
    return DoctorItem(
        category="Runtime",
        name="Python Version",
        status=CheckStatus.ERROR,
        details=f"Python {version_str} detected",
        recommendation="Upgrade to Python 3.10 or higher: sudo apt install python3 python3-venv",
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


def _probe_dns(host: str, timeout: float = 2.0) -> bool:
    """Probes DNS resolution with a strict timeout via ThreadPoolExecutor."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(socket.getaddrinfo, host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
            future.result(timeout=timeout)
            return True
    except Exception:
        return False


def _probe_tcp_connect(host: str, port: int = 443, timeout: float = 2.5) -> bool:
    """Probes TCP connection with a short timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def check_github_reachability() -> DoctorItem:
    """Verifies reachability to GitHub (port 443)."""
    dns_ok = _probe_dns("github.com", timeout=2.0)
    if not dns_ok:
        return DoctorItem(
            category="Network",
            name="GitHub Reachability",
            status=CheckStatus.WARNING,
            details="github.com DNS resolution failed",
            recommendation="Verify server DNS resolver and outbound connectivity for github.com.",
        )

    tcp_ok = _probe_tcp_connect("github.com", 443, timeout=2.5)
    if tcp_ok:
        return DoctorItem(
            category="Network",
            name="GitHub Reachability",
            status=CheckStatus.OK,
            details="github.com reachable (HTTPS:443)",
        )
    return DoctorItem(
        category="Network",
        name="GitHub Reachability",
        status=CheckStatus.WARNING,
        details="github.com connection failed or timed out",
        recommendation="Verify outbound HTTPS (port 443) connectivity or firewall rules.",
    )


def check_pypi_reachability() -> DoctorItem:
    """Verifies reachability to Python package index (PyPI or custom mirror)."""
    custom_index = os.getenv("PIP_INDEX_URL")
    if custom_index:
        parsed = urlparse(custom_index)
        host = parsed.hostname or "custom-mirror"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        is_custom = True
    else:
        host = "pypi.org"
        port = 443
        is_custom = False

    dns_ok = _probe_dns(host, timeout=2.0)
    tcp_ok = _probe_tcp_connect(host, port, timeout=2.5) if dns_ok else False

    if tcp_ok:
        label = f"Custom mirror ({host})" if is_custom else "pypi.org"
        return DoctorItem(
            category="Network",
            name="Python Package Index",
            status=CheckStatus.OK,
            details=f"{label} reachable",
        )

    details = f"Cannot reach package index host '{host}'"
    rec = "Check network or configure a reachable mirror: export PIP_INDEX_URL=https://<mirror>/simple/"
    return DoctorItem(
        category="Network",
        name="Python Package Index",
        status=CheckStatus.WARNING,
        details=details,
        recommendation=rec,
    )


def check_dns_resolution() -> DoctorItem:
    """
    Performs lightweight DNS resolution checks against key deployment hosts:
    github.com, pypi.org, files.pythonhosted.org.
    Detects inconsistent or partial DNS reachability.
    """
    hosts = ["github.com", "pypi.org", "files.pythonhosted.org"]
    custom_index = os.getenv("PIP_INDEX_URL")
    if custom_index:
        parsed = urlparse(custom_index)
        if parsed.hostname and parsed.hostname not in hosts:
            hosts.append(parsed.hostname)

    resolved: list[str] = []
    failed: list[str] = []

    for host in hosts:
        if _probe_dns(host, timeout=2.0):
            resolved.append(host)
        else:
            failed.append(host)

    if not failed:
        return DoctorItem(
            category="Network",
            name="DNS Resolution",
            status=CheckStatus.OK,
            details=f"All {len(hosts)} checked hosts resolved successfully",
        )
    elif resolved and failed:
        return DoctorItem(
            category="Network",
            name="DNS Resolution",
            status=CheckStatus.WARNING,
            details="Partial DNS/network reachability detected. Some package/CDN hosts are not reachable.",
            recommendation=f"Failed hosts: {', '.join(failed)}. Verify /etc/resolv.conf or test with custom PIP_INDEX_URL mirror.",
        )
    else:
        return DoctorItem(
            category="Network",
            name="DNS Resolution",
            status=CheckStatus.WARNING,
            details="DNS resolution failed for all test hosts.",
            recommendation="Server cannot resolve public domains. Check network configuration and DNS nameservers.",
        )


def check_orphan_resources() -> DoctorItem:
    """
    Scans for Docker resources carrying DeployX management labels or naming conventions
    whose associated project is no longer registered. (Requirement 14, 15)
    """
    from deployx.deployment.project import project_exists
    from deployx.docker.compose import find_managed_containers, find_managed_volumes

    orphan_projects: dict[str, dict[str, int]] = {}

    try:
        # Containers
        for c in find_managed_containers():
            lbl = c.get("labels", "")
            pname = None
            for p in lbl.split(","):
                if p.strip().startswith("com.deployx.project="):
                    pname = p.strip().split("=")[1].strip()
                    break
            if not pname and c.get("name", "").startswith("deployx_"):
                parts = c["name"].split("_")
                if len(parts) >= 2:
                    pname = parts[1]
            if pname and not project_exists(pname):
                if pname not in orphan_projects:
                    orphan_projects[pname] = {"containers": 0, "volumes": 0}
                orphan_projects[pname]["containers"] += 1

        # Volumes
        for v in find_managed_volumes():
            lbl = v.get("labels", "")
            pname = None
            for p in lbl.split(","):
                if p.strip().startswith("com.deployx.project="):
                    pname = p.strip().split("=")[1].strip()
                    break
            if not pname and v.get("name", "").startswith("deployx_"):
                parts = v["name"].split("_")
                if len(parts) >= 2:
                    pname = parts[1]
            if pname and not project_exists(pname):
                if pname not in orphan_projects:
                    orphan_projects[pname] = {"containers": 0, "volumes": 0}
                orphan_projects[pname]["volumes"] += 1

    except Exception:
        pass

    if orphan_projects:
        details_list = [
            f"{p} ({counts['containers']} containers, {counts['volumes']} volumes)"
            for p, counts in orphan_projects.items()
        ]
        return DoctorItem(
            category="Docker",
            name="Orphan Resources",
            status=CheckStatus.WARNING,
            details=f"Unregistered project resources detected: {', '.join(details_list)}",
            recommendation="Clean orphan resources using 'deployx project remove <project> --purge' or Docker CLI.",
        )
    return DoctorItem(
        category="Docker",
        name="Orphan Resources",
        status=CheckStatus.OK,
        details="No orphan DeployX containers or volumes detected",
    )


def check_docker_container_network() -> DoctorItem:
    """
    Tests DNS resolution and network reachability from inside a temporary Docker container.
    """
    from deployx.core.command import run_command
    cmd = [
        "docker", "run", "--rm",
        "alpine:latest",
        "sh", "-c", "ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1 && nslookup github.com >/dev/null 2>&1"
    ]
    try:
        res = run_command(cmd, timeout=10, check=False)
        if res.success:
            return DoctorItem(
                category="Docker",
                name="Container Network",
                status=CheckStatus.OK,
                details="Docker containers have functional outbound DNS and internet connectivity",
            )
        else:
            return DoctorItem(
                category="Docker",
                name="Container Network",
                status=CheckStatus.WARNING,
                details="In-container outbound DNS or internet access failed",
                recommendation="Docker container network failed to resolve public DNS. Configure DNS in /etc/docker/daemon.json (e.g. {\"dns\": [\"8.8.8.8\"]}) and restart docker.",
            )
    except Exception as exc:
        return DoctorItem(
            category="Docker",
            name="Container Network",
            status=CheckStatus.WARNING,
            details=f"Could not verify container network: {exc}",
            recommendation="Ensure Docker daemon is running and can pull or run containers.",
        )


def collect_doctor_checks(check_network: bool = False, orphans: bool = False) -> List[DoctorItem]:
    """Runs all doctor checks and returns the list of results."""
    items = [
        check_ubuntu_version(),
        check_python_version(),
        check_git(),
        check_ssh_client(),
        check_docker_engine(),
        check_docker_compose_v2(),
        check_docker_daemon(),
        check_disk_space(),
        check_deployx_directories(),
        check_github_reachability(),
        check_pypi_reachability(),
        check_dns_resolution(),
    ]
    if orphans:
        items.append(check_orphan_resources())
    if check_network:
        items.append(check_docker_container_network())
    return items


def run_doctor(
    console: Console,
    check_network: bool = False,
    orphans: bool = False,
) -> bool:
    """
    Executes doctor diagnostic checks and prints a formatted Rich report.
    Returns True if no ERROR checks were encountered, False otherwise.
    """
    console.print("\n[bold cyan]DeployX Doctor Diagnostic Suite[/bold cyan]")
    console.print("[dim]Scanning host environment and dependencies...[/dim]\n")

    items = collect_doctor_checks(check_network=check_network, orphans=orphans)

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


def run_project_doctor(
    project_name: str,
    check_network: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Runs project-specific diagnostics: config validation, git/key access, database and mirror config.
    """
    if console is None:
        console = Console()

    from deployx.core.security import validate_project_name
    from deployx.deployment.project import inspect_project_config
    from deployx.state import get_state_manager

    try:
        valid_name = validate_project_name(project_name)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return False

    console.print(f"\n[bold cyan]DeployX Project Doctor: {valid_name}[/bold cyan]")
    console.print("[dim]Checking project configuration, credentials, and infrastructure...[/dim]\n")

    items: List[DoctorItem] = []

    # 1. Config Check
    inspection = inspect_project_config(valid_name)
    if not inspection.valid:
        items.append(
            DoctorItem(
                category="Project",
                name="Configuration",
                status=CheckStatus.ERROR,
                details="deployx.yml contains validation errors",
                recommendation=f"Run 'deployx project edit {valid_name}' to fix errors:\n{inspection.error_message}",
            )
        )
    else:
        cfg = inspection.config
        items.append(
            DoctorItem(
                category="Project",
                name="Configuration",
                status=CheckStatus.OK,
                details=f"Valid deployx.yml (framework: {cfg.deployment.framework.value}, database: {cfg.deployment.database.value})",
            )
        )

        # 2. Git & Key Check
        if cfg.git.private:
            key_path = paths.get_project_key_path(valid_name)
            if not key_path.exists():
                items.append(
                    DoctorItem(
                        category="Git",
                        name="Deploy Key",
                        status=CheckStatus.ERROR,
                        details="Private repository configured but SSH deploy key is missing",
                        recommendation=f"Run 'deployx key create {valid_name}' and add to GitHub Deploy Keys.",
                    )
                )
            else:
                items.append(
                    DoctorItem(
                        category="Git",
                        name="Deploy Key",
                        status=CheckStatus.OK,
                        details=f"Deploy key present at {key_path}",
                    )
                )
        else:
            items.append(
                DoctorItem(
                    category="Git",
                    name="Repository Access",
                    status=CheckStatus.OK,
                    details=f"Public repository ({cfg.git.repository})",
                )
            )

        # 3. State Check
        state = get_state_manager().get_state(valid_name)
        if state:
            st = CheckStatus.OK if state.status.value in ("healthy", "pending") else CheckStatus.WARNING
            items.append(
                DoctorItem(
                    category="Deployment",
                    name="Current State",
                    status=st,
                    details=f"Status: {state.status.value}, Commit: {state.current_commit[:7] if state.current_commit else 'None'}",
                )
            )

    # 4. Optional network test
    if check_network:
        items.append(check_docker_container_network())

    # Render table
    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Category", style="cyan", width=14)
    table.add_column("Check", style="bold", width=24)
    table.add_column("Status", width=12)
    table.add_column("Details", style="white")

    err_count = 0
    for item in items:
        if item.status == CheckStatus.OK:
            s_text = "[bold green][ OK ][/bold green]"
        elif item.status == CheckStatus.WARNING:
            s_text = "[bold yellow][ WARN ][/bold yellow]"
        else:
            s_text = "[bold red][ ERROR ][/bold red]"
            err_count += 1
        table.add_row(item.category, item.name, s_text, item.details)

    console.print(table)
    return err_count == 0
