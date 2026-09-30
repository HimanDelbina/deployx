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

    def get_project_backups_dir(self, project_name: str) -> Path:
        pdir = self.get_project_dir(project_name)
        target = pdir / "backups"
        return prevent_path_traversal(pdir, target)


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


def load_global_config() -> dict[str, Any]:
    """
    Safely loads global settings from /etc/deployx/config.yml (or DEPLOYX_CONFIG_DIR / config.yml).
    Returns an empty dict if the file is missing or contains invalid YAML.
    """
    import yaml

    cfg_file = paths.config_dir / "config.yml"
    if not cfg_file.is_file():
        return {}
    try:
        content = cfg_file.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_effective_build_timeout(
    cli_build_timeout: Optional[int] = None,
    no_build_timeout: bool = False,
    project_config: Optional[Any] = None,
) -> Optional[int]:
    """
    Resolves the effective Docker build timeout following strict precedence:
    1. CLI override: --no-build-timeout -> None (unlimited)
    2. CLI override: --build-timeout <seconds>
    3. Project config: deployment.build_timeout
    4. Global config: deployment.build_timeout in /etc/deployx/config.yml
    5. Default: 3600 seconds
    """
    if no_build_timeout:
        return None
    if cli_build_timeout is not None:
        return cli_build_timeout

    if project_config is not None:
        dep = getattr(project_config, "deployment", None)
        if dep is not None and getattr(dep, "build_timeout", None) is not None:
            return dep.build_timeout

    global_cfg = load_global_config()
    dep_global = global_cfg.get("deployment")
    if isinstance(dep_global, dict):
        timeout_val = dep_global.get("build_timeout")
        if timeout_val is not None:
            try:
                return int(timeout_val)
            except (ValueError, TypeError):
                pass

    return 3600


def get_effective_python_build_config(
    project_config: Optional[Any] = None,
) -> dict[str, Optional[str]]:
    """
    Resolves Python package mirror configuration following strict precedence:
    1. Environment variables: PIP_INDEX_URL, PIP_EXTRA_INDEX_URL, PIP_TRUSTED_HOST
    2. Project config: build.python.*
    3. Global config: build.python.* in /etc/deployx/config.yml
    4. Default: None (standard PyPI)
    """
    resolved: dict[str, Optional[str]] = {
        "index_url": None,
        "extra_index_url": None,
        "trusted_host": None,
    }

    # 3. Global config
    global_cfg = load_global_config()
    build_global = global_cfg.get("build")
    if isinstance(build_global, dict):
        py_global = build_global.get("python")
        if isinstance(py_global, dict):
            resolved["index_url"] = py_global.get("index_url") or None
            resolved["extra_index_url"] = py_global.get("extra_index_url") or None
            resolved["trusted_host"] = py_global.get("trusted_host") or None

    # 2. Project config
    if project_config is not None:
        py_proj = getattr(getattr(project_config, "build", None), "python", None)
        if py_proj is not None:
            if getattr(py_proj, "index_url", None):
                resolved["index_url"] = py_proj.index_url
            if getattr(py_proj, "extra_index_url", None):
                resolved["extra_index_url"] = py_proj.extra_index_url
            if getattr(py_proj, "trusted_host", None):
                resolved["trusted_host"] = py_proj.trusted_host

    # 1. Environment variables
    if os.environ.get("PIP_INDEX_URL"):
        resolved["index_url"] = os.environ["PIP_INDEX_URL"]
    if os.environ.get("PIP_EXTRA_INDEX_URL"):
        resolved["extra_index_url"] = os.environ["PIP_EXTRA_INDEX_URL"]
    if os.environ.get("PIP_TRUSTED_HOST"):
        resolved["trusted_host"] = os.environ["PIP_TRUSTED_HOST"]

    return resolved


def backup_project_file(file_path: Path, max_backups: int = 5) -> Optional[Path]:
    """
    Safely creates a timestamped backup of a file in a 'backups' directory next to it.
    Applies mode 0600 and rotates keeping at most max_backups.
    """
    import datetime
    import shutil
    from deployx.core.filesystem import ensure_directory, set_secure_permissions

    if not file_path.is_file():
        return None

    backups_dir = file_path.parent / "backups"
    ensure_directory(backups_dir, mode=0o700)

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_file = backups_dir / f"{file_path.stem}_{timestamp}{file_path.suffix}"

    shutil.copy2(file_path, backup_file)
    set_secure_permissions(backup_file, mode=0o600)

    # Rotation: keep most recent max_backups
    pattern = f"{file_path.stem}_*{file_path.suffix}"
    existing = sorted(backups_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    if len(existing) > max_backups:
        for old_file in existing[:-max_backups]:
            try:
                old_file.unlink(missing_ok=True)
            except Exception:
                pass

    return backup_file
