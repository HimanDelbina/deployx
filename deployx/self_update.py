"""
DeployX Autonomous Self-Update Engine
Fetches official release metadata, performs safe virtualenv upgrades,
validates upgraded binary health, and supports automatic rollback on failure.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from rich.console import Console
from rich.panel import Panel

from deployx import __version__
from deployx.config import paths
from deployx.core.command import run_command

GITHUB_RELEASES_API = "https://api.github.com/repos/HimanDelbina/deployx/releases"
GITHUB_REPO_URL = "https://github.com/HimanDelbina/deployx.git"


def get_latest_release(channel: str = "stable", timeout: int = 10) -> Tuple[Optional[str], Optional[str]]:
    """
    Queries GitHub API to find the latest release tag and download URL.
    Returns (tag_name, html_url).
    """
    try:
        req = urllib.request.Request(
            GITHUB_RELEASES_API,
            headers={
                "User-Agent": "DeployX-SelfUpdate/0.2",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, list) or not data:
                return None, None

            for rel in data:
                is_prerelease = rel.get("prerelease", False)
                if channel == "stable" and is_prerelease:
                    continue
                tag = rel.get("tag_name")
                url = rel.get("html_url")
                if tag:
                    return tag, url
    except Exception:
        # Fallback to querying git ls-remote tags
        try:
            res = run_command(["git", "ls-remote", "--tags", GITHUB_REPO_URL], timeout=timeout)
            if res.returncode == 0:
                tags = []
                for line in res.stdout.splitlines():
                    if "refs/tags/" in line:
                        tag_name = line.split("refs/tags/")[-1].replace("^{}", "").strip()
                        if tag_name.startswith("v"):
                            tags.append(tag_name)
                if tags:
                    return tags[-1], f"{GITHUB_REPO_URL}/releases/tag/{tags[-1]}"
        except Exception:
            pass

    return None, None


def perform_self_update(
    channel: str = "stable",
    force: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Executes zero-touch DeployX self-update:
    1. Checks latest remote version against local version.
    2. Identifies current python executable / virtualenv.
    3. Performs pip upgrade from official git repository tag.
    4. Runs smoke test (`deployx --version`).
    5. Preserves all project configs, states, keys, and volumes.
    """
    con = console or Console()
    con.print(f"[cyan]Checking for DeployX updates (channel: {channel})...[/cyan]")

    latest_tag, release_url = get_latest_release(channel=channel)
    if not latest_tag:
        con.print("[yellow]Could not reach GitHub release API. Checking current system...[/yellow]")
        con.print(f"Current version is [bold green]v{__version__}[/bold green].")
        return False

    clean_latest = latest_tag.lstrip("v")
    clean_curr = __version__.lstrip("v")

    if clean_latest == clean_curr and not force:
        con.print(f"[bold green]DeployX is already up to date[/bold green] (v{__version__}).")
        return True

    con.print(
        Panel(
            f"[bold]New DeployX version available![/bold]\n\n"
            f"Current: [yellow]v{__version__}[/yellow]\n"
            f"Latest:  [bold green]{latest_tag}[/bold green]\n"
            f"Release: {release_url or 'GitHub'}",
            title="DeployX Self-Update",
            border_style="cyan",
        )
    )

    # Resolve pip executable
    pip_cmd = sys.executable
    venv_pip = paths.root_dir / "venv" / "bin" / "pip"
    venv_pip_win = paths.root_dir / "venv" / "Scripts" / "pip.exe"
    pip_bin = None

    if venv_pip.is_file():
        pip_bin = str(venv_pip)
    elif venv_pip_win.is_file():
        pip_bin = str(venv_pip_win)
    else:
        # Fallback to current python -m pip
        pip_bin = sys.executable

    con.print(f"[cyan]Upgrading DeployX to {latest_tag} using {pip_bin}...[/cyan]")

    install_target = f"git+{GITHUB_REPO_URL}@{latest_tag}"
    cmd = [pip_bin, "install", "--upgrade", install_target] if not pip_bin.endswith("python") else [pip_bin, "-m", "pip", "install", "--upgrade", install_target]

    res = run_command(cmd, timeout=300)
    if res.returncode != 0:
        con.print(f"[bold red]Self-update failed during pip install:[/bold red]\n{res.stderr or res.stdout}")
        return False

    # Smoke test new version
    smoke_cmd = [sys.executable, "-m", "deployx.cli", "--version"]
    smoke_res = run_command(smoke_cmd, timeout=15)
    if smoke_res.returncode == 0:
        con.print(f"[bold green]Successfully upgraded to DeployX {latest_tag}![/bold green]")
        return True
    else:
        con.print(f"[bold red]Smoke test failed after upgrade. Rolling back...[/bold red]")
        rollback_target = f"git+{GITHUB_REPO_URL}@v{clean_curr}"
        run_command([pip_bin, "install", "--upgrade", rollback_target], timeout=300)
        return False


class SelfUpdateManager:
    """Manages self-update checks and execution."""

    def __init__(self, channel: str = "stable"):
        self.channel = channel

    def check_for_update(self) -> Tuple[bool, Optional[str]]:
        latest_tag, _ = get_latest_release(channel=self.channel)
        if not latest_tag:
            return False, None
        clean_latest = latest_tag.lstrip("v")
        clean_curr = __version__.lstrip("v")
        return clean_latest != clean_curr, latest_tag

    def perform_update(self, force: bool = False, console: Optional[Console] = None) -> Tuple[bool, str]:
        ok = perform_self_update(channel=self.channel, force=force, console=console)
        if ok:
            return True, "DeployX updated successfully."
        return False, "DeployX update could not be completed."

