"""
DeployX State Management Abstraction
Provides pluggable state backends. In v0.1, file-based JSON state is implemented
with atomic writes, prepared for future SQLite/PostgreSQL backends.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from deployx.config import paths
from deployx.core.filesystem import atomic_write_file, ensure_directory
from deployx.models import DeploymentState


class BaseStateBackend(ABC):
    """Abstract interface for deployment state persistence."""

    @abstractmethod
    def get_state(self, project: str) -> Optional[DeploymentState]:
        """Loads state for a given project. Returns None if no state exists."""
        pass

    @abstractmethod
    def save_state(self, state: DeploymentState) -> None:
        """Persists the deployment state atomically."""
        pass

    @abstractmethod
    def delete_state(self, project: str) -> bool:
        """Deletes state for a project."""
        pass


class JsonFileStateBackend(BaseStateBackend):
    """
    JSON file backend storing state files at /opt/deployx/state/<project>.state.json.
    """

    def _state_file(self, project: str) -> Path:
        return paths.get_project_state_path(project)

    def get_state(self, project: str) -> Optional[DeploymentState]:
        state_path = self._state_file(project)
        if not state_path.is_file():
            return None
        try:
            content = state_path.read_text(encoding="utf-8")
            data = json.loads(content)
            return DeploymentState.model_validate(data)
        except Exception:
            return None

    def save_state(self, state: DeploymentState) -> None:
        ensure_directory(paths.state_dir)
        state_path = self._state_file(state.project)
        payload = state.model_dump_json(indent=2)
        atomic_write_file(state_path, payload, mode=0o640)

    def delete_state(self, project: str) -> bool:
        state_path = self._state_file(project)
        if state_path.exists():
            state_path.unlink()
            return True
        return False


# Default backend instance
state_backend: BaseStateBackend = JsonFileStateBackend()


def get_state_manager() -> BaseStateBackend:
    return state_backend
