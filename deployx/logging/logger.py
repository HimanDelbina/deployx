"""
DeployX Per-Project Deployment Logger
Writes isolated logs to /opt/deployx/logs/<project>/deploy.log.
Enforces secret redaction on all log messages.
"""

from __future__ import annotations

import datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from deployx.config import paths
from deployx.core.filesystem import ensure_directory
from deployx.core.security import redact_sensitive_text, validate_project_name


class ProjectLogger:
    """Writes timestamped, redacted execution logs to /opt/deployx/logs/<project>/deploy.log with rotation."""

    def __init__(self, project_name: str, max_bytes: int = 10 * 1024 * 1024, backup_count: int = 5):
        self.project_name = validate_project_name(project_name)
        self.log_dir = paths.get_project_log_dir(self.project_name)
        ensure_directory(self.log_dir, mode=0o750)
        self.log_file = self.log_dir / "deploy.log"

        self.logger = logging.getLogger(f"deployx.project.{self.project_name}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        # Configure rotating file handler if not already present
        if not self.logger.handlers:
            handler = RotatingFileHandler(
                str(self.log_file),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            formatter = logging.Formatter("[%(asctime)s UTC] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def log(self, message: str, level: str = "INFO") -> None:
        clean_msg = redact_sensitive_text(message)
        lvl_upper = level.upper()
        if lvl_upper == "DEBUG":
            self.logger.debug(clean_msg)
        elif lvl_upper in ("WARN", "WARNING"):
            self.logger.warning(clean_msg)
        elif lvl_upper == "ERROR":
            self.logger.error(clean_msg)
        else:
            self.logger.info(clean_msg)

    def close(self) -> None:
        """Closes and removes all handlers associated with this project logger."""
        for handler in list(self.logger.handlers):
            try:
                handler.close()
                self.logger.removeHandler(handler)
            except Exception:
                pass

    def info(self, msg: str) -> None:
        self.log(msg, level="INFO")

    def warn(self, msg: str) -> None:
        self.log(msg, level="WARN")

    def error(self, msg: str) -> None:
        self.log(msg, level="ERROR")

    def log_event(
        self,
        stage: str,
        command: Optional[str] = None,
        exit_code: Optional[int] = None,
        elapsed: Optional[float] = None,
        build_step: Optional[str] = None,
        health_status: Optional[str] = None,
        commit: Optional[str] = None,
    ) -> None:
        """Writes a structured deployment audit event with secrets redacted."""
        parts = [f"project={self.project_name}", f"stage={stage}"]
        if commit:
            parts.append(f"commit={commit}")
        if command:
            clean_cmd = redact_sensitive_text(command)
            parts.append(f"command='{clean_cmd}'")
        if exit_code is not None:
            parts.append(f"exit_code={exit_code}")
        if elapsed is not None:
            parts.append(f"elapsed={elapsed:.2f}s")
        if build_step:
            parts.append(f"build_step='{build_step}'")
        if health_status:
            parts.append(f"health={health_status}")

        event_str = " | ".join(parts)
        self.info(f"EVENT: {event_str}")
