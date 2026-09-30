"""
DeployX Port Discovery & Conflict Resolution
Detects port conflicts on the host and automatically resolves available socket ports.
"""

from __future__ import annotations

import socket
from typing import Optional, Set

from deployx.config import paths
from deployx.deployment.project import load_project_config


def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    """
    Checks if a TCP port is in use or cannot be bound on the given host.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return False
        except OSError:
            return True


def get_all_allocated_project_ports(exclude_project: Optional[str] = None) -> Set[int]:
    """
    Scans all registered DeployX project configs to collect already allocated host ports.
    """
    allocated: Set[int] = set()
    projects_dir = paths.projects_dir
    if not projects_dir.is_dir():
        return allocated

    for p in projects_dir.iterdir():
        if p.is_dir() and (p / "config.yml").is_file():
            if exclude_project and p.name == exclude_project:
                continue
            try:
                cfg = load_project_config(p.name)
                if cfg.deployment and cfg.deployment.docker and cfg.deployment.docker.port:
                    allocated.add(cfg.deployment.docker.port)
            except Exception:
                continue
    return allocated


def find_available_port(
    preferred_port: int = 8000,
    max_port: int = 8999,
    exclude_project: Optional[str] = None,
) -> int:
    """
    Finds the first available port starting from preferred_port.
    Avoids both actively bound host ports and ports assigned to other DeployX projects.
    """
    allocated = get_all_allocated_project_ports(exclude_project=exclude_project)

    for port in range(preferred_port, max_port + 1):
        if port in allocated:
            continue
        if not is_port_in_use(port):
            return port

    # Fallback search if preferred range exhausted
    for port in range(9000, 9999):
        if port not in allocated and not is_port_in_use(port):
            return port

    raise RuntimeError(f"No available TCP ports found in range {preferred_port}-{max_port}")
