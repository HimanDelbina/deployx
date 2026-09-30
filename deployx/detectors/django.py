"""
DeployX Django Framework & Infrastructure Detector
Identifies Django projects, WSGI/ASGI entrypoints, settings modules,
database dependencies (PostgreSQL), caching/queuing (Redis, Celery),
and existing Docker/Compose/Env infrastructure.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Set

from deployx.detectors.base import BaseDetector, DetectedInfrastructure, DetectionResult
from deployx.models import FrameworkType


class DjangoDetector(BaseDetector):
    """Detector for Django web applications."""

    @property
    def framework(self) -> FrameworkType:
        return FrameworkType.DJANGO

    def _extract_dependencies(self, repo_path: Path) -> Set[str]:
        """Collects lowercased package names from requirements.txt, pyproject.toml, etc."""
        deps = set()

        # 1. requirements.txt (and variants)
        req_files = list(repo_path.glob("requirements*.txt")) + list(repo_path.glob("requirements/*.txt"))
        for req_path in req_files:
            try:
                for line in req_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    clean = line.strip().split("#")[0].strip()
                    if clean and not clean.startswith("-"):
                        # Remove extras e.g. psycopg[binary] -> psycopg
                        clean_no_extras = re.sub(r"\[.*?\]", "", clean)
                        pkg = re.split(r"[><=~!@\s]", clean_no_extras)[0].lower()
                        if pkg:
                            deps.add(pkg)
            except Exception:
                pass

        # 2. pyproject.toml & Pipfile
        for manifest_file in [repo_path / "pyproject.toml", repo_path / "Pipfile"]:
            if manifest_file.is_file():
                try:
                    content = manifest_file.read_text(encoding="utf-8", errors="ignore")
                    for token in re.findall(r'["\']([^"\']+)["\']', content):
                        # Strip extras e.g. [binary]
                        clean_token = re.sub(r"\[.*?\]", "", token).strip()
                        pkg = re.split(r"[><=~!^@\s,]", clean_token)[0].strip().lower()
                        if pkg and re.match(r"^[a-z0-9][a-z0-9_\-\.]*$", pkg):
                            deps.add(pkg)
                except Exception:
                    pass

        return deps

    def _find_wsgi_module(self, repo_path: Path) -> Optional[str]:
        """Finds wsgi.py or asgi.py and infers the python module path."""
        for wsgi_file in repo_path.glob("**/wsgi.py"):
            rel = wsgi_file.relative_to(repo_path)
            # Skip hidden dirs or virtualenvs
            if any(part.startswith(".") or part in {"venv", ".venv", "env", "node_modules"} for part in rel.parts):
                continue
            parent = wsgi_file.parent.relative_to(repo_path)
            module_name = str(parent).replace("\\", ".").replace("/", ".")
            return f"{module_name}.wsgi:application"
        return None

    def _find_settings_module(self, repo_path: Path) -> Optional[str]:
        """Finds settings.py and infers the DJANGO_SETTINGS_MODULE value."""
        for settings_file in repo_path.glob("**/settings*.py"):
            rel = settings_file.relative_to(repo_path)
            if any(part.startswith(".") or part in {"venv", ".venv", "env", "node_modules"} for part in rel.parts):
                continue
            # If inside settings/base.py -> myproject.settings.base
            parts = list(rel.parts)
            parts[-1] = parts[-1].replace(".py", "")
            return ".".join(parts)
        return None

    def detect(self, repo_path: Path) -> DetectionResult:
        """Inspects repository codebase for Django indicators and infrastructure."""
        indicators: List[str] = []
        confidence = 0.0

        if not repo_path.is_dir():
            return DetectionResult(
                framework=self.framework,
                confidence=0.0,
                matched_indicators=[],
                infrastructure=DetectedInfrastructure(),
            )

        # 1. Look for manage.py
        manage_py = repo_path / "manage.py"
        if not manage_py.is_file():
            # Check depth 1 (e.g. src/manage.py)
            src_manage = repo_path / "src" / "manage.py"
            if src_manage.is_file():
                manage_py = src_manage

        if manage_py.is_file():
            indicators.append("manage.py")
            confidence += 0.5
            try:
                content = manage_py.read_text(encoding="utf-8", errors="ignore")
                if "django" in content.lower():
                    confidence += 0.2
            except Exception:
                pass

        # 2. Extract dependencies
        deps = self._extract_dependencies(repo_path)
        if "django" in deps:
            indicators.append("django dependency in manifest")
            confidence += 0.3

        # 3. Settings module
        settings_mod = self._find_settings_module(repo_path)
        if settings_mod:
            indicators.append(f"settings module ({settings_mod})")
            confidence += 0.2

        # 4. WSGI module
        wsgi_mod = self._find_wsgi_module(repo_path)
        if wsgi_mod:
            indicators.append(f"wsgi module ({wsgi_mod})")
            confidence += 0.1

        # Cap confidence at 1.0
        confidence = min(confidence, 1.0)

        # 5. Infrastructure detection
        # Check Dockerfile
        dockerfile_path = None
        for df_candidate in [repo_path / "Dockerfile", repo_path / "docker" / "Dockerfile"]:
            if df_candidate.is_file():
                dockerfile_path = df_candidate
                break

        # Check Compose
        compose_path = None
        for cp_candidate in [
            repo_path / "docker-compose.yml",
            repo_path / "docker-compose.yaml",
            repo_path / "compose.yml",
            repo_path / "compose.yaml",
        ]:
            if cp_candidate.is_file():
                compose_path = cp_candidate
                break

        # Check .env.example
        env_example_path = None
        for env_candidate in [
            repo_path / ".env.example",
            repo_path / ".env.sample",
            repo_path / "example.env",
            repo_path / ".env.template",
        ]:
            if env_candidate.is_file():
                env_example_path = env_candidate
                break

        # Import shared detection helpers
        from deployx.detectors.base import (
            analyze_env_contract,
            detect_health_endpoint,
            detect_package_manager,
            detect_python_version,
        )

        # Detect Python version & package manager
        py_ver, py_constraint, py_reason = detect_python_version(repo_path)
        pkg_mgr, install_cmd, pkg_reason = detect_package_manager(repo_path)
        health_ep, health_reason = detect_health_endpoint(repo_path)
        env_contract = analyze_env_contract(repo_path)

        # Check workers & scheduler
        has_celery_file = any(
            not any(p.startswith(".") or p in {"venv", ".venv", "env"} for p in f.parts)
            for f in repo_path.glob("**/celery.py")
        )
        has_celery = bool({"celery", "django-celery-beat", "django-celery-results"} & deps) or has_celery_file
        has_rq = bool({"django-rq", "rq"} & deps)
        has_beat = bool({"django-celery-beat"} & deps)
        has_redis = bool({"redis", "django-redis", "redis-py"} & deps) or has_celery or has_rq
        has_gunicorn = bool({"gunicorn", "uvicorn", "daphne", "granian", "waitress"} & deps)

        # Check database preference & reason
        has_postgres = bool(
            {"psycopg2", "psycopg2-binary", "psycopg", "asyncpg", "dj-database-url"} & deps
        )
        has_mysql = bool({"mysqlclient", "pymysql"} & deps)

        db_type = "postgres"
        db_reason = "Default production database"
        if has_postgres:
            db_type = "postgres"
            db_reason = "PostgreSQL drivers (psycopg/dj-database-url) detected in project dependencies"
        elif has_mysql:
            db_type = "mysql"
            db_reason = "MySQL drivers detected in project dependencies"
        else:
            # Check settings content
            for sf in repo_path.glob("**/settings*.py"):
                try:
                    s_content = sf.read_text(encoding="utf-8", errors="ignore")
                    if "django.db.backends.postgresql" in s_content:
                        db_type = "postgres"
                        db_reason = f"django.db.backends.postgresql configured in {sf.name}"
                        has_postgres = True
                        break
                    elif "django.db.backends.sqlite3" in s_content and not has_postgres:
                        db_type = "sqlite"
                        db_reason = f"django.db.backends.sqlite3 configured in {sf.name}"
                except Exception:
                    pass

        # Media directory detection
        has_media = (repo_path / "media").is_dir()
        if not has_media:
            for sf in repo_path.glob("**/settings*.py"):
                try:
                    if "MEDIA_ROOT" in sf.read_text(encoding="utf-8", errors="ignore"):
                        has_media = True
                        break
                except Exception:
                    pass

        infra = DetectedInfrastructure(
            has_dockerfile=dockerfile_path is not None,
            dockerfile_path=dockerfile_path,
            has_compose=compose_path is not None,
            compose_path=compose_path,
            has_env_example=env_example_path is not None,
            env_example_path=env_example_path,
            has_postgres=has_postgres or (db_type == "postgres"),
            has_redis=has_redis,
            has_celery=has_celery,
            has_rq=has_rq,
            has_beat=has_beat,
            has_media=has_media,
            has_gunicorn=has_gunicorn,
            dependencies=sorted(list(deps)),
            wsgi_module=wsgi_mod,
            settings_module=settings_mod,
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
            "framework": f"Django detected based on {', '.join(indicators)}",
            "runtime": py_reason,
            "package_manager": pkg_reason,
            "database": db_reason,
            "redis": "Redis required for Celery/caching" if has_redis else "Redis not required",
            "workers": f"Background worker ({'Celery' if has_celery else 'RQ'}) detected" if (has_celery or has_rq) else "No background workers required",
            "health": health_reason,
            "media": "Persistent media volume enabled (MEDIA_ROOT/media directory detected)" if has_media else "No persistent media volume required",
        }

        return DetectionResult(
            framework=self.framework,
            confidence=confidence,
            matched_indicators=indicators,
            infrastructure=infra,
            details={
                "has_manage_py": manage_py.is_file(),
                "manage_py_path": str(manage_py.relative_to(repo_path)) if manage_py.is_file() else None,
            },
            explanation=explanation,
        )
