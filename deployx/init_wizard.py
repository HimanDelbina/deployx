"""
DeployX Server Initialization Wizard
Performs initial system configuration, creates directory structures, sets permissions,
benchmarks mirrors, and initializes /etc/deployx/config.yml.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deployx.config import paths, load_global_config
from deployx.core.command import run_command
from deployx.core.filesystem import ensure_directory, set_secure_permissions, atomic_write_file
from deployx.network.mirrors import auto_select_best_mirror, persist_selected_mirror


def run_init_wizard(
    non_interactive: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Initializes DeployX on the Ubuntu server.
    """
    if console is None:
        console = Console()

    console.print(
        Panel(
            "[bold cyan]DeployX Server Initialization Wizard[/bold cyan]\n\n"
            "Setting up /opt/deployx directories, permissions, mirror benchmarking, and global configuration.",
            title="DeployX Init",
            border_style="cyan",
        )
    )

    # 1. Directory Structure Creation
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Directory", style="cyan")
    table.add_column("Permission")
    table.add_column("Status")

    dirs = [
        (paths.root_dir, 0o750),
        (paths.projects_dir, 0o750),
        (paths.keys_dir, 0o700),
        (paths.backups_dir, 0o700),
        (paths.logs_dir, 0o750),
        (paths.state_dir, 0o750),
        (paths.generated_dir, 0o750),
        (paths.config_dir, 0o755),
    ]

    for d, mode in dirs:
        try:
            ensure_directory(d, mode=mode)
            table.add_row(str(d), oct(mode), "[green]Created/Verified[/green]")
        except Exception as exc:
            table.add_row(str(d), oct(mode), f"[red]Failed: {exc}[/red]")

    console.print(table)

    # 2. PyPI Mirror Selection
    console.print("\n[dim]Benchmarking Python package mirrors for zero-touch speed...[/dim]")
    try:
        best_mirror, probe_results = auto_select_best_mirror(timeout=3.0, persist=True)
        console.print(f"[bold green]Selected PyPI mirror:[/bold green] {best_mirror}")
    except Exception as exc:
        console.print(f"[yellow]Mirror benchmark warning: {exc}. Using default PyPI.[/yellow]")

    # 3. Global Config Verification
    global_cfg_file = paths.config_dir / "config.yml"
    if not global_cfg_file.is_file():
        default_config = (
            "# DeployX Global Configuration\n"
            "version: '0.2.0'\n"
            "defaults:\n"
            "  framework: django\n"
            "  database: postgres\n"
            "  build_timeout: 3600\n"
            "  auto_rollback: true\n"
            "network:\n"
            "  proxy:\n"
            "    enabled: true\n"
            "    type: caddy\n"
        )
        try:
            atomic_write_file(global_cfg_file, default_config, mode=0o644)
            console.print(f"[green]Initialized global config at {global_cfg_file}[/green]")
        except Exception as exc:
            console.print(f"[yellow]Could not write global config: {exc}[/yellow]")
    else:
        console.print(f"[green]Global config present at {global_cfg_file}[/green]")

    console.print(
        Panel(
            "[bold green]DeployX initialization completed successfully![/bold green]\n\n"
            "You are ready to deploy your first project:\n"
            "  [bold cyan]deployx deploy git@github.com:user/project.git[/bold cyan]",
            title="Init Complete",
            border_style="green",
        )
    )
    return True
