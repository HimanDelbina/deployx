"""
DeployX Global Configuration and Path Management.
Supports environment variable overrides for testability and portability:
- DEPLOYX_ROOT (defaults to /opt/deployx)
- DEPLOYX_CONFIG_DIR (defaults to /etc/deployx)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from deployx.core.filesystem import ensure_directory
from deployx.core.security import prevent_path_traversal, validate_project_name


@dataclass
class SystemPaths:
    root_dir: Path
    config_dir: Path
    projects_dir: Path
    keys_dir: Path
    backups_dir: Path
    logs_dir: Path
    state_dir: Path
    generated_dir: Path

    @classmethod
    def default(cls) -> SystemPaths:
        root = Path(os.getenv("DEPLOYX_ROOT", "/opt/deployx")).resolve()
        config_dir = Path(os.getenv("DEPLOYX_CONFIG_DIR", "/etc/deployx")).resolve()
        return cls(
            root_dir=root,
            config_dir=config_dir,
            projects_dir=root / "projects",
            keys_dir=root / "keys",
            backups_dir=root / "backups",
            logs_dir=root / "logs",
            state_dir=root / "state",
            generated_dir=root / "generated",
        )

    def ensure_all_dirs(self) -> None:
        """Creates all standard DeployX directory trees with safe permissions."""
        ensure_directory(self.root_dir, mode=0o755)
        ensure_directory(self.projects_dir, mode=0o750)
        # keys directory must have strict 0700 permissions
        ensure_directory(self.keys_dir, mode=0o700)
        ensure_directory(self.backups_dir, mode=0o700)
        ensure_directory(self.logs_dir, mode=0o750)
        ensure_directory(self.state_dir, mode=0o750)
        ensure_directory(self.generated_dir, mode=0o750)
        ensure_directory(self.config_dir, mode=0o755)

    def get_project_dir(self, project_name: str) -> Path:
        valid_name = validate_project_name(project_name)
        target = self.projects_dir / valid_name
        return prevent_path_traversal(self.projects_dir, target)

    def get_project_repo_dir(self, project_name: str) -> Path:
        pdir = self.get_project_dir(project_name)
        target = pdir / "repo"
        return prevent_path_traversal(pdir, target)

    def get_project_config_path(self, project_name: str) -> Path:
        pdir = self.get_project_dir(project_name)
        return pdir / "deployx.yml"

    def get_project_key_path(self, project_name: str) -> Path:
        valid_name = validate_project_name(project_name)
        target = self.keys_dir / valid_name
        return prevent_path_traversal(self.keys_dir, target)

    def get_project_pubkey_path(self, project_name: str) -> Path:
        valid_name = validate_project_name(project_name)
        target = self.keys_dir / f"{valid_name}.pub"
        return prevent_path_traversal(self.keys_dir, target)

    def get_project_log_dir(self, project_name: str) -> Path:
        valid_name = validate_project_name(project_name)
        target = self.logs_dir / valid_name
        return prevent_path_traversal(self.logs_dir, target)

    def get_project_state_path(self, project_name: str) -> Path:
        valid_name = validate_project_name(project_name)
        target = self.state_dir / f"{valid_name}.state.json"
        return prevent_path_traversal(self.state_dir, target)


# Global singleton instance
paths = SystemPaths.default()


def reload_paths() -> SystemPaths:
    """Reloads paths from current environment variables in-place (useful in tests)."""
    global paths
    new_paths = SystemPaths.default()
    paths.root_dir = new_paths.root_dir
    paths.config_dir = new_paths.config_dir
    paths.projects_dir = new_paths.projects_dir
    paths.keys_dir = new_paths.keys_dir
    paths.backups_dir = new_paths.backups_dir
    paths.logs_dir = new_paths.logs_dir
    paths.state_dir = new_paths.state_dir
    paths.generated_dir = new_paths.generated_dir
    return paths
