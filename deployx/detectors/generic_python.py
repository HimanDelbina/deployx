"""
DeployX Generic Python Web Application Detector
Fallback detector for Python WSGI/ASGI services.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Set

from deployx.detectors.base import (
    BaseDetector,
    DetectedInfrastructure,
    DetectionResult,
    analyze_env_contract,
    detect_health_endpoint,
    detect_package_manager,
    detect_python_version,
    extract_dependencies_from_repo,
)
from deployx.models import FrameworkType


class GenericPythonDetector(BaseDetector):
    """Fallback detector for generic Python WSGI/ASGI web applications."""

    @property
    def framework(self) -> FrameworkType:
        return FrameworkType.GENERIC_PYTHON

    def _extract_dependencies(self, repo_path: Path) -> Set[str]:
        return extract_dependencies_from_repo(repo_path)

    def detect(self, repo_path: Path) -> DetectionResult:
        if not repo_path.is_dir():
            return DetectionResult(
                framework=self.framework,
                confidence=0.0,
                matched_indicators=[],
                infrastructure=DetectedInfrastructure(),
            )

        indicators: List[str] = []
        confidence = 0.0

        py_files = list(repo_path.glob("*.py")) + list(repo_path.glob("src/*.py"))
        if py_files:
            indicators.append(f"Python source files present ({len(py_files)})")
            confidence += 0.2

        deps = self._extract_dependencies(repo_path)
        if deps:
            indicators.append(f"Python dependencies manifest ({len(deps)} packages)")
            confidence += 0.15

        confidence = min(confidence, 0.35)

        if confidence == 0.0:
            return DetectionResult(
                framework=self.framework,
                confidence=0.0,
                matched_indicators=[],
                infrastructure=DetectedInfrastructure(),
            )

        py_ver, py_constraint, py_reason = detect_python_version(repo_path)
        pkg_mgr, install_cmd, pkg_reason = detect_package_manager(repo_path)
        health_ep, health_reason = detect_health_endpoint(repo_path)
        env_contract = analyze_env_contract(repo_path)

        dockerfile_path = repo_path / "Dockerfile" if (repo_path / "Dockerfile").is_file() else None
        compose_path = repo_path / "docker-compose.yml" if (repo_path / "docker-compose.yml").is_file() else None
        env_example_path = repo_path / ".env.example" if (repo_path / ".env.example").is_file() else None

        has_postgres = bool({"psycopg2", "psycopg2-binary", "psycopg", "asyncpg"} & deps)
        has_redis = bool({"redis"} & deps)

        infra = DetectedInfrastructure(
            has_dockerfile=dockerfile_path is not None,
            dockerfile_path=dockerfile_path,
            has_compose=compose_path is not None,
            compose_path=compose_path,
            has_env_example=env_example_path is not None,
            env_example_path=env_example_path,
            has_postgres=has_postgres,
            has_redis=has_redis,
            has_gunicorn=bool({"gunicorn", "uvicorn", "waitress"} & deps),
            dependencies=sorted(list(deps)),
            wsgi_module="main:app",
            python_version_req=py_constraint,
            selected_python_version=py_ver,
            package_manager=pkg_mgr,
            install_command=install_cmd,
            database="postgres" if has_postgres else "none",
            database_reason="PostgreSQL driver detected" if has_postgres else "No database dependencies detected",
            health_endpoint=health_ep,
            env_contract=env_contract,
        )

        return DetectionResult(
            framework=self.framework,
            confidence=confidence,
            matched_indicators=indicators,
            infrastructure=infra,
            explanation={
                "framework": "Generic Python application",
                "runtime": py_reason,
                "package_manager": pkg_reason,
                "health": health_reason,
            },
        )
