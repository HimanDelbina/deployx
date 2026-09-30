"""
DeployX FastAPI Framework & Infrastructure Detector
Identifies FastAPI applications, ASGI entrypoints, dependencies,
and database/caching requirements.
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


class FastAPIDetector(BaseDetector):
    """Detector for FastAPI web applications."""

    @property
    def framework(self) -> FrameworkType:
        return FrameworkType.FASTAPI

    def _extract_dependencies(self, repo_path: Path) -> Set[str]:
        return extract_dependencies_from_repo(repo_path)

    def _find_asgi_app(self, repo_path: Path) -> Optional[str]:
        """Finds main.py or app.py containing FastAPI() instance."""
        candidates = ["main.py", "app.py", "app/main.py", "src/main.py", "src/app.py"]
        for rel_str in candidates:
            cand = repo_path / rel_str
            if cand.is_file():
                try:
                    content = cand.read_text(encoding="utf-8", errors="ignore")
                    if "FastAPI(" in content:
                        module_name = rel_str.replace(".py", "").replace("/", ".").replace("\\", ".")
                        # Look for variable name, usually app = FastAPI()
                        m = re.search(r"(\w+)\s*=\s*FastAPI\(", content)
                        var_name = m.group(1) if m else "app"
                        return f"{module_name}:{var_name}"
                except Exception:
                    pass
        return "main:app"

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

        deps = self._extract_dependencies(repo_path)
        if "fastapi" in deps:
            indicators.append("fastapi dependency")
            confidence += 0.5

        # Check for FastAPI imports or instantiate in python files
        asgi_entry = None
        for py_file in repo_path.glob("**/*.py"):
            rel = py_file.relative_to(repo_path)
            if any(p.startswith(".") or p in {"venv", ".venv", "env"} for p in rel.parts):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if "from fastapi import" in content or "import fastapi" in content:
                    indicators.append(f"FastAPI import in {rel}")
                    confidence += 0.3
                    if "FastAPI(" in content and not asgi_entry:
                        mod = str(rel).replace(".py", "").replace("/", ".").replace("\\", ".")
                        m = re.search(r"(\w+)\s*=\s*FastAPI\(", content)
                        var_name = m.group(1) if m else "app"
                        asgi_entry = f"{mod}:{var_name}"
                    break
            except Exception:
                pass

        if not asgi_entry:
            asgi_entry = self._find_asgi_app(repo_path)

        confidence = min(confidence, 1.0)
        if confidence < 0.4:
            return DetectionResult(
                framework=self.framework,
                confidence=confidence,
                matched_indicators=indicators,
                infrastructure=DetectedInfrastructure(),
            )

        py_ver, py_constraint, py_reason = detect_python_version(repo_path)
        pkg_mgr, install_cmd, pkg_reason = detect_package_manager(repo_path)
        health_ep, health_reason = detect_health_endpoint(repo_path)
        env_contract = analyze_env_contract(repo_path)

        has_postgres = bool({"asyncpg", "psycopg2", "psycopg2-binary", "psycopg", "databases", "sqlalchemy"} & deps)
        has_redis = bool({"redis", "aioredis"} & deps)
        has_celery = bool({"celery"} & deps)

        db_type = "postgres" if has_postgres else "none"
        db_reason = "PostgreSQL async driver detected" if has_postgres else "No database dependencies detected"

        dockerfile_path = repo_path / "Dockerfile" if (repo_path / "Dockerfile").is_file() else None
        compose_path = repo_path / "docker-compose.yml" if (repo_path / "docker-compose.yml").is_file() else None
        env_example_path = repo_path / ".env.example" if (repo_path / ".env.example").is_file() else None

        infra = DetectedInfrastructure(
            has_dockerfile=dockerfile_path is not None,
            dockerfile_path=dockerfile_path,
            has_compose=compose_path is not None,
            compose_path=compose_path,
            has_env_example=env_example_path is not None,
            env_example_path=env_example_path,
            has_postgres=has_postgres,
            has_redis=has_redis,
            has_celery=has_celery,
            has_gunicorn=bool({"uvicorn", "gunicorn", "granian"} & deps),
            dependencies=sorted(list(deps)),
            asgi_module=asgi_entry,
            python_version_req=py_constraint,
            selected_python_version=py_ver,
            package_manager=pkg_mgr,
            install_command=install_cmd,
            database=db_type,
            database_reason=db_reason,
            health_endpoint=health_ep,
            env_contract=env_contract,
        )

        explanation = {
            "framework": f"FastAPI detected based on {', '.join(indicators)}",
            "runtime": py_reason,
            "package_manager": pkg_reason,
            "database": db_reason,
            "redis": "Redis required for caching/queuing" if has_redis else "Redis not required",
            "workers": "Celery worker detected" if has_celery else "No background workers detected",
            "health": health_reason,
        }

        return DetectionResult(
            framework=self.framework,
            confidence=confidence,
            matched_indicators=indicators,
            infrastructure=infra,
            explanation=explanation,
        )
