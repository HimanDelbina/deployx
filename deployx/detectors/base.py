"""
DeployX Base Detector Contracts
Provides data models and abstract interfaces for modular framework and
infrastructure detection.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from deployx.models import FrameworkType


@dataclass
class DetectedInfrastructure:
    """Infrastructure components detected inside the project repository."""
    has_dockerfile: bool = False
    dockerfile_path: Optional[Path] = None
    has_compose: bool = False
    compose_path: Optional[Path] = None
    has_env_example: bool = False
    env_example_path: Optional[Path] = None
    has_postgres: bool = False
    has_redis: bool = False
    has_celery: bool = False
    has_gunicorn: bool = False
    dependencies: List[str] = field(default_factory=list)
    wsgi_module: Optional[str] = None
    asgi_module: Optional[str] = None
    settings_module: Optional[str] = None


@dataclass
class DetectionResult:
    """Outcome of analyzing a repository codebase."""
    framework: FrameworkType
    confidence: float  # 0.0 to 1.0
    matched_indicators: List[str]
    infrastructure: DetectedInfrastructure
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.6


class BaseDetector(ABC):
    """Abstract base class for framework-specific detectors."""

    @property
    @abstractmethod
    def framework(self) -> FrameworkType:
        """The framework handled by this detector."""
        pass

    @abstractmethod
    def detect(self, repo_path: Path) -> DetectionResult:
        """Analyzes repo_path and returns detection result with confidence."""
        pass
