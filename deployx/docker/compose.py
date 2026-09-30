"""
DeployX Docker Compose v2 Manager
Strictly uses Docker Compose v2 (`docker compose`, not legacy `docker-compose`).
Provides process-safe execution of container lifecycle commands.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, List, Optional

from rich.console import Console

from deployx.config import paths
from deployx.core.command import CommandError, CommandResult, run_command, run_command_streaming
from deployx.core.security import validate_project_name


class DockerComposeManager:
    """Manages Docker Compose v2 lifecycle operations for a specific project."""

    def __init__(self, project_name: str, compose_file: Optional[Path] = None):
        self.project_name = validate_project_name(project_name)
        self.project_dir = paths.get_project_dir(self.project_name)
        self.compose_file = compose_file or (self.project_dir / "docker-compose.deployx.yml")

    def _base_cmd(self) -> List[str]:
        return ["docker", "compose", "-f", str(self.compose_file)]

    def build(
        self,
        service: Optional[str] = None,
        no_cache: bool = False,
        stream: bool = True,
        on_line: Optional[Any] = None,
        on_heartbeat: Optional[Any] = None,
        on_stall: Optional[Any] = None,
        log_file: Optional[Path | str] = None,
        timeout: Optional[int] = 3600,
        env: Optional[dict[str, str]] = None,
    ) -> CommandResult:
        """
        Runs 'docker compose --progress=plain -f file.yml build' with BuildKit output.
        When stream=True, streams output incrementally to prevent silent freezes.
        """
        cmd = ["docker", "compose", "--progress=plain", "-f", str(self.compose_file), "build"]
        if no_cache:
            cmd.append("--no-cache")
        if service:
            cmd.append(service)

        build_env = {
            "DOCKER_BUILDKIT": "1",
            "BUILDKIT_PROGRESS": "plain",
            "PROGRESS_NO_TRUNC": "1",
        }
        if env:
            build_env.update(env)

        if stream:
            return run_command_streaming(
                cmd,
                cwd=self.project_dir,
                env=build_env,
                timeout=timeout,
                on_line=on_line,
                on_heartbeat=on_heartbeat,
                on_stall=on_stall,
                log_file=log_file,
            )
        return run_command(cmd, cwd=self.project_dir, env=build_env, timeout=timeout)

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
        stream: bool = False,
        on_line: Optional[Any] = None,
        log_file: Optional[Path | str] = None,
    ) -> CommandResult:
        """
        Runs a one-off command in a service container without starting the service daemon.
        Uses 'docker compose run --rm -T <service> <command>'.
        Supports real-time output streaming when stream=True.
        """
        from deployx.core.command import run_command_streaming

        cmd = self._base_cmd() + ["run", "--rm", "-T", service] + command
        if stream:
            return run_command_streaming(
                cmd,
                cwd=self.project_dir,
                timeout=timeout,
                on_line=on_line,
                log_file=log_file,
            )
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


def check_image_exists(image_tag: str) -> bool:
    """Checks whether a Docker image exists locally."""
    try:
        res = run_command(["docker", "image", "inspect", image_tag], timeout=15, check=False)
        return res.returncode == 0
    except Exception:
        return False


def find_managed_containers(project_name: Optional[str] = None) -> List[dict[str, str]]:
    """
    Finds Docker containers managed by DeployX via labels or naming conventions.
    """
    results: List[dict[str, str]] = []
    seen_ids = set()

    # Query 1: by label
    try:
        res = run_command(
            ["docker", "ps", "-a", "--filter", "label=com.deployx.managed=true", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Labels}}"],
            timeout=20,
            check=False,
        )
        if res.success and res.stdout.strip():
            for line in res.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 4:
                    cid, name, status, img = parts[0], parts[1], parts[2], parts[3]
                    labels = parts[4] if len(parts) > 4 else ""
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        results.append({"id": cid, "name": name, "status": status, "image": img, "labels": labels})
    except Exception:
        pass

    # Query 2: by name pattern if project_name specified
    if project_name:
        try:
            prefix = f"deployx_{project_name}_"
            res = run_command(
                ["docker", "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Labels}}"],
                timeout=20,
                check=False,
            )
            if res.success and res.stdout.strip():
                for line in res.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 4:
                        cid, name, status, img = parts[0], parts[1], parts[2], parts[3]
                        labels = parts[4] if len(parts) > 4 else ""
                        if cid not in seen_ids:
                            seen_ids.add(cid)
                            results.append({"id": cid, "name": name, "status": status, "image": img, "labels": labels})
        except Exception:
            pass

    if project_name:
        filtered = []
        for c in results:
            lbl = c.get("labels", "")
            if f"com.deployx.project={project_name}" in lbl or c["name"].startswith(f"deployx_{project_name}_") or c["name"] == f"deployx_{project_name}":
                filtered.append(c)
        return filtered

    return results


def find_managed_volumes(project_name: Optional[str] = None) -> List[dict[str, str]]:
    """
    Finds Docker volumes managed by DeployX via labels or naming conventions.
    """
    results: List[dict[str, str]] = []
    seen_names = set()

    try:
        res = run_command(
            ["docker", "volume", "ls", "--filter", "label=com.deployx.managed=true", "--format", "{{.Name}}\t{{.Labels}}"],
            timeout=20,
            check=False,
        )
        if res.success and res.stdout.strip():
            for line in res.stdout.splitlines():
                parts = line.split("\t")
                if parts and parts[0]:
                    vname = parts[0]
                    vlabels = parts[1] if len(parts) > 1 else ""
                    if vname not in seen_names:
                        seen_names.add(vname)
                        results.append({"name": vname, "labels": vlabels})
    except Exception:
        pass

    if project_name:
        try:
            prefix = f"deployx_{project_name}_"
            res = run_command(
                ["docker", "volume", "ls", "--filter", f"name={prefix}", "--format", "{{.Name}}\t{{.Labels}}"],
                timeout=20,
                check=False,
            )
            if res.success and res.stdout.strip():
                for line in res.stdout.splitlines():
                    parts = line.split("\t")
                    if parts and parts[0]:
                        vname = parts[0]
                        vlabels = parts[1] if len(parts) > 1 else ""
                        if vname not in seen_names:
                            seen_names.add(vname)
                            results.append({"name": vname, "labels": vlabels})
        except Exception:
            pass

    if project_name:
        filtered = []
        for v in results:
            lbl = v.get("labels", "")
            if f"com.deployx.project={project_name}" in lbl or v["name"].startswith(f"deployx_{project_name}_"):
                filtered.append(v)
        return filtered

    return results


def find_managed_images(project_name: Optional[str] = None) -> List[dict[str, str]]:
    """
    Finds Docker images managed by DeployX via labels or naming conventions.
    """
    results: List[dict[str, str]] = []
    seen_ids = set()

    try:
        res = run_command(
            ["docker", "images", "--filter", "label=com.deployx.managed=true", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Labels}}"],
            timeout=20,
            check=False,
        )
        if res.success and res.stdout.strip():
            for line in res.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    ref, iid = parts[0], parts[1]
                    labels = parts[2] if len(parts) > 2 else ""
                    if iid not in seen_ids:
                        seen_ids.add(iid)
                        results.append({"ref": ref, "id": iid, "labels": labels})
    except Exception:
        pass

    if project_name:
        try:
            res = run_command(
                ["docker", "images", f"deployx_{project_name}*", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Labels}}"],
                timeout=20,
                check=False,
            )
            if res.success and res.stdout.strip():
                for line in res.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        ref, iid = parts[0], parts[1]
                        labels = parts[2] if len(parts) > 2 else ""
                        if iid not in seen_ids:
                            seen_ids.add(iid)
                            results.append({"ref": ref, "id": iid, "labels": labels})
        except Exception:
            pass

    if project_name:
        filtered = []
        for img in results:
            lbl = img.get("labels", "")
            if f"com.deployx.project={project_name}" in lbl or img["ref"].startswith(f"deployx_{project_name}:") or img["ref"].startswith(f"deployx_{project_name}_"):
                filtered.append(img)
        return filtered

    return results


def find_managed_networks(project_name: Optional[str] = None) -> List[dict[str, str]]:
    """
    Finds Docker networks managed by DeployX via labels or naming conventions.
    """
    results: List[dict[str, str]] = []
    seen_names = set()

    try:
        res = run_command(
            ["docker", "network", "ls", "--filter", "label=com.deployx.managed=true", "--format", "{{.Name}}\t{{.ID}}\t{{.Labels}}"],
            timeout=20,
            check=False,
        )
        if res.success and res.stdout.strip():
            for line in res.stdout.splitlines():
                parts = line.split("\t")
                if parts and parts[0]:
                    nname = parts[0]
                    nid = parts[1] if len(parts) > 1 else ""
                    nlabels = parts[2] if len(parts) > 2 else ""
                    if nname not in seen_names:
                        seen_names.add(nname)
                        results.append({"name": nname, "id": nid, "labels": nlabels})
    except Exception:
        pass

    if project_name:
        try:
            prefix = f"deployx_{project_name}_"
            res = run_command(
                ["docker", "network", "ls", "--filter", f"name={prefix}", "--format", "{{.Name}}\t{{.ID}}\t{{.Labels}}"],
                timeout=20,
                check=False,
            )
            if res.success and res.stdout.strip():
                for line in res.stdout.splitlines():
                    parts = line.split("\t")
                    if parts and parts[0]:
                        nname = parts[0]
                        nid = parts[1] if len(parts) > 1 else ""
                        nlabels = parts[2] if len(parts) > 2 else ""
                        if nname not in seen_names:
                            seen_names.add(nname)
                            results.append({"name": nname, "id": nid, "labels": nlabels})
        except Exception:
            pass

    if project_name:
        filtered = []
        for n in results:
            lbl = n.get("labels", "")
            if f"com.deployx.project={project_name}" in lbl or n["name"].startswith(f"deployx_{project_name}_"):
                filtered.append(n)
        return filtered

    return results


def purge_project_resources(
    project_name: str,
    remove_containers: bool = True,
    remove_images: bool = True,
    remove_volumes: bool = False,
    remove_networks: bool = True,
) -> dict[str, int]:
    """
    Safely purges Docker resources for a project using labels and naming conventions.
    Does NOT depend on docker-compose.deployx.yml existing.
    """
    counts = {"containers": 0, "images": 0, "volumes": 0, "networks": 0}

    # 1. Containers
    if remove_containers:
        for c in find_managed_containers(project_name):
            try:
                run_command(["docker", "rm", "-f", c["id"]], timeout=30, check=False)
                counts["containers"] += 1
            except Exception:
                pass

    # 2. Networks
    if remove_networks:
        for n in find_managed_networks(project_name):
            try:
                run_command(["docker", "network", "rm", n["name"]], timeout=15, check=False)
                counts["networks"] += 1
            except Exception:
                pass

    # 3. Volumes
    if remove_volumes:
        for v in find_managed_volumes(project_name):
            try:
                run_command(["docker", "volume", "rm", "-f", v["name"]], timeout=20, check=False)
                counts["volumes"] += 1
            except Exception:
                pass

    # 4. Images
    if remove_images:
        for img in find_managed_images(project_name):
            try:
                target = img["ref"] if (img["ref"] and img["ref"] != "<none>:<none>") else img["id"]
                run_command(["docker", "rmi", "-f", target], timeout=30, check=False)
                counts["images"] += 1
            except Exception:
                pass

    return counts
