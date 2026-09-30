"""
DeployX Managed Reverse Proxy & Automatic HTTPS Engine
Manages reverse proxy routing (Caddy) and automatic Let's Encrypt TLS certificates.
Keeps reverse proxy configuration strictly decoupled from application code.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Dict, Optional

from rich.console import Console

from deployx.config import paths
from deployx.core.command import run_command
from deployx.core.filesystem import atomic_write_file, ensure_directory
from deployx.core.security import validate_domain, validate_project_name


class ProxyManager:
    """Manages Caddy reverse proxy routing and automatic HTTPS."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self.proxy_dir = paths.root_dir / "proxy"
        self.caddyfile = self.proxy_dir / "Caddyfile"
        self.sites_dir = self.proxy_dir / "sites"

    def ensure_initialized(self) -> None:
        ensure_directory(self.proxy_dir, mode=0o755)
        ensure_directory(self.sites_dir, mode=0o755)
        if not self.caddyfile.exists():
            main_caddy = (
                "# ==============================================================================\n"
                "# DeployX Managed Global Caddyfile\n"
                "# Auto-generated reverse proxy configuration for projects.\n"
                "# ==============================================================================\n\n"
                "import sites/*\n"
            )
            atomic_write_file(self.caddyfile, main_caddy, mode=0o644)

    def check_dns_resolution(self, domain: str) -> tuple[bool, Optional[str]]:
        """
        Validates if domain DNS resolves to an IP address before requesting TLS certificates.
        """
        try:
            ip = socket.gethostbyname(domain)
            return True, ip
        except Exception as exc:
            return False, str(exc)

    def configure_project_domain(
        self,
        project_name: str,
        domain: str,
        target_port: int = 8000,
        enable_https: bool = True,
    ) -> bool:
        """
        Creates or updates a Caddy route for a project.
        """
        valid_pname = validate_project_name(project_name)
        valid_domain = validate_domain(domain)
        if not valid_domain:
            raise ValueError(f"Invalid domain name: {domain}")

        self.ensure_initialized()
        site_file = self.sites_dir / f"{valid_pname}.caddy"

        # Check DNS resolution
        resolves, res_info = self.check_dns_resolution(valid_domain)
        if not resolves:
            self.console.print(
                f"[yellow]Warning: Domain '{valid_domain}' does not yet resolve to an IP ({res_info}).[/yellow]\n"
                "Automatic HTTPS will activate once public DNS records point to this server."
            )

        protocol_prefix = ""
        if not enable_https:
            protocol_prefix = "http://"

        caddy_snippet = (
            f"# Managed by DeployX for project {valid_pname}\n"
            f"{protocol_prefix}{valid_domain} {{\n"
            f"    reverse_proxy 127.0.0.1:{target_port}\n"
            f"}}\n"
        )
        atomic_write_file(site_file, caddy_snippet, mode=0o644)
        return True

    def remove_project_domain(self, project_name: str) -> bool:
        """Removes the Caddy route for a project."""
        valid_pname = validate_project_name(project_name)
        site_file = self.sites_dir / f"{valid_pname}.caddy"
        if site_file.exists():
            site_file.unlink(missing_ok=True)
            return True
        return False

    def add_or_update_route(
        self,
        project_name: str,
        domain: str,
        target_port: int = 8000,
        enable_https: bool = True,
    ) -> bool:
        """Alias for configure_project_domain."""
        return self.configure_project_domain(project_name, domain, target_port, enable_https)

    def remove_route(self, identifier: str) -> bool:
        """Removes route by project name or matching domain."""
        try:
            valid_pname = validate_project_name(identifier)
            site_file = self.sites_dir / f"{valid_pname}.caddy"
            if site_file.exists():
                site_file.unlink(missing_ok=True)
                return True
        except Exception:
            pass

        # Check if identifier matches content in any caddy site file
        if self.sites_dir.exists():
            for site_file in self.sites_dir.glob("*.caddy"):
                try:
                    content = site_file.read_text(encoding="utf-8")
                    if identifier in content:
                        site_file.unlink(missing_ok=True)
                        return True
                except Exception:
                    pass
        return False

    def get_project_domain(self, project_name: str) -> Optional[str]:
        """Retrieves configured domain for a project if route exists."""
        valid_pname = validate_project_name(project_name)
        site_file = self.sites_dir / f"{valid_pname}.caddy"
        if not site_file.exists():
            return None
        try:
            content = site_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "{" in line:
                    return line.split("{")[0].strip().replace("http://", "").replace("https://", "")
        except Exception:
            pass
        return None
