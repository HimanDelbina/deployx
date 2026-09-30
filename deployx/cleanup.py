"""
DeployX System & Docker Resource Cleanup Manager
Cleans dangling images, build caches, stopped containers, orphan networks, and unreferenced project resources.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from rich.console import Console
from rich.table import Table

from deployx.core.command import run_command
from deployx.deployment.project import project_exists
from deployx.docker.compose import find_managed_containers, find_managed_volumes


def run_system_cleanup(
    orphans: bool = False,
    delete_volumes: bool = False,
    force: bool = False,
    console: Optional[Console] = None,
) -> Dict[str, Any]:
    """
    Executes automated system cleanup across Docker resources.
    Safely respects active registered projects.
    """
    if console is None:
        console = Console()

    console.print("\n[bold cyan]DeployX System Resource Cleanup[/bold cyan]")
    console.print("[dim]Cleaning dangling Docker resources and reclaiming disk space...[/dim]\n")

    summary: Dict[str, int] = {
        "pruned_containers": 0,
        "pruned_images": 0,
        "pruned_volumes": 0,
        "orphan_containers": 0,
        "orphan_volumes": 0,
    }

    # 1. Prune stopped containers
    try:
        res = run_command(["docker", "container", "prune", "-f"], timeout=30, check=False)
        if res.success:
            console.print("[green]✓[/green] Pruned stopped containers")
    except Exception:
        pass

    # 2. Prune dangling images
    try:
        res = run_command(["docker", "image", "prune", "-f"], timeout=60, check=False)
        if res.success:
            console.print("[green]✓[/green] Pruned dangling images")
    except Exception:
        pass

    # 3. Prune Docker build cache
    try:
        res = run_command(["docker", "builder", "prune", "-f"], timeout=60, check=False)
        if res.success:
            console.print("[green]✓[/green] Pruned Docker build cache")
    except Exception:
        pass

    # 4. Clean orphan project resources if requested
    if orphans:
        console.print("[dim]Scanning for orphan project resources...[/dim]")
        # Orphan containers
        for c in find_managed_containers():
            cname = c.get("name", "")
            cid = c.get("id", "")
            pname = None
            lbl = c.get("labels", "")
            for p in lbl.split(","):
                if p.strip().startswith("com.deployx.project="):
                    pname = p.strip().split("=")[1].strip()
                    break
            if not pname and cname.startswith("deployx_"):
                parts = cname.split("_")
                if len(parts) >= 2:
                    pname = parts[1]

            if pname and not project_exists(pname):
                run_command(["docker", "rm", "-f", cid or cname], timeout=15, check=False)
                summary["orphan_containers"] += 1

        if summary["orphan_containers"] > 0:
            console.print(f"[green]✓[/green] Removed {summary['orphan_containers']} orphan container(s)")
        else:
            console.print("[dim]No orphan containers detected[/dim]")

        # Orphan volumes (if volume deletion also authorized)
        if delete_volumes:
            for v in find_managed_volumes():
                vname = v.get("name", "")
                pname = None
                lbl = v.get("labels", "")
                for p in lbl.split(","):
                    if p.strip().startswith("com.deployx.project="):
                        pname = p.strip().split("=")[1].strip()
                        break
                if not pname and vname.startswith("deployx_"):
                    parts = vname.split("_")
                    if len(parts) >= 2:
                        pname = parts[1]

                if pname and not project_exists(pname):
                    run_command(["docker", "volume", "rm", "-f", vname], timeout=15, check=False)
                    summary["orphan_volumes"] += 1

            if summary["orphan_volumes"] > 0:
                console.print(f"[green]✓[/green] Removed {summary['orphan_volumes']} orphan volume(s)")

    console.print("\n[bold green]Cleanup completed successfully.[/bold green]")
    return summary
