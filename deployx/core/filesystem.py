"""
DeployX Filesystem Operations
Provides secure directory creation, safe permissions setting, and atomic file writes.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def ensure_directory(path: Path | str, mode: int = 0o750) -> Path:
    """
    Ensures directory exists with specified permissions.
    """
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    set_secure_permissions(p, mode)
    return p


def set_secure_permissions(path: Path | str, mode: int = 0o600) -> None:
    """
    Sets file/directory permissions safely.
    On POSIX systems (Linux/macOS), calls os.chmod.
    On Windows, gracefully handles differences while maintaining file access.
    """
    p = Path(path)
    if not p.exists():
        return
    
    if os.name == "posix":
        try:
            os.chmod(p, mode)
        except OSError:
            pass


def atomic_write_file(target_file: Path | str, content: str, mode: int = 0o600) -> Path:
    """
    Writes content to target_file atomically:
    1. Writes to temporary file in the same directory.
    2. Sets secure permissions.
    3. Flushes and syncs to disk.
    4. Performs atomic rename (os.replace).
    """
    dest = Path(target_file).resolve()
    parent_dir = dest.parent
    ensure_directory(parent_dir)
    
    # Create temp file in same directory to guarantee same filesystem for atomic rename
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=parent_dir,
        delete=False,
        encoding="utf-8",
        prefix=".tmp_deployx_"
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)
        tmp_file.write(content)
        tmp_file.flush()
        try:
            os.fsync(tmp_file.fileno())
        except (AttributeError, OSError):
            pass
    
    set_secure_permissions(tmp_path, mode)
    
    # Atomic replace
    tmp_path.replace(dest)
    set_secure_permissions(dest, mode)
    return dest


def safe_read_file(target_file: Path | str) -> str:
    """
    Safely reads file contents as UTF-8.
    """
    p = Path(target_file)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")
    return p.read_text(encoding="utf-8")
