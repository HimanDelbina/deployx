"""
DeployX Per-Project Deployment Logger
Writes isolated logs to /opt/deployx/logs/<project>/deploy.log.
Enforces secret redaction on all log messages.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from deployx.config import paths
from deployx.core.filesystem import ensure_directory
from deployx.core.security import redact_sensitive_text, validate_project_name


class ProjectLogger:
    """Writes timestamped, redacted execution logs to /opt/deployx/logs/<project>/deploy.log."""

    def __init__(self, project_name: str):
        self.project_name = validate_project_name(project_name)
        self.log_dir = paths.get_project_log_dir(self.project_name)
        ensure_directory(self.log_dir, mode=0o750)
        self.log_file = self.log_dir / "deploy.log"

    def log(self, message: str, level: str = "INFO") -> None:
        clean_msg = redact_sensitive_text(message)
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        entry = f"[{now_str}] [{level.upper()}] {clean_msg}\n"
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(entry)

    def info(self, msg: str) -> None:
        self.log(msg, level="INFO")

    def warn(self, msg: str) -> None:
        self.log(msg, level="WARN")

    def error(self, msg: str) -> None:
        self.log(msg, level="ERROR")
