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
    has_rq: bool = False
    has_beat: bool = False
    has_media: bool = False
    has_gunicorn: bool = False
    dependencies: List[str] = field(default_factory=list)
    wsgi_module: Optional[str] = None
    asgi_module: Optional[str] = None
    settings_module: Optional[str] = None
    python_version_req: Optional[str] = None
    selected_python_version: str = "3.12"
    package_manager: str = "pip"
    install_command: List[str] = field(default_factory=lambda: ["pip", "install", "-r", "requirements.txt"])
    database: str = "postgres"
    database_reason: str = "default"
    health_endpoint: str = "/"
    env_contract: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DetectionResult:
    """Outcome of analyzing a repository codebase."""
    framework: FrameworkType
    confidence: float  # 0.0 to 1.0
    matched_indicators: List[str]
    infrastructure: DetectedInfrastructure
    details: Dict[str, Any] = field(default_factory=dict)
    explanation: Dict[str, str] = field(default_factory=dict)

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


def detect_python_version(repo_path: Path) -> tuple[str, Optional[str], str]:
    """
    Inspects repository for Python runtime requirements and selects the optimal
    supported Python base image version (3.10, 3.11, 3.12, 3.13).
    Returns (selected_version, constraint_string, rationale).
    """
    import re

    # 1. runtime.txt (Heroku / cloud style, e.g. python-3.11.4)
    runtime_file = repo_path / "runtime.txt"
    if runtime_file.is_file():
        try:
            content = runtime_file.read_text(encoding="utf-8").strip()
            m = re.search(r"python-(\d+\.\d+)", content, re.IGNORECASE)
            if m:
                ver = m.group(1)
                for sup in ("3.13", "3.12", "3.11", "3.10"):
                    if ver.startswith(sup):
                        return (sup, content, f"runtime.txt specifies {content} -> selected Python {sup}")
        except Exception:
            pass

    # 2. .python-version (pyenv style, e.g. 3.11.9 or 3.12)
    pyver_file = repo_path / ".python-version"
    if pyver_file.is_file():
        try:
            content = pyver_file.read_text(encoding="utf-8").strip().splitlines()[0]
            m = re.search(r"(\d+\.\d+)", content)
            if m:
                ver = m.group(1)
                for sup in ("3.13", "3.12", "3.11", "3.10"):
                    if ver.startswith(sup):
                        return (sup, content, f".python-version specifies {content} -> selected Python {sup}")
        except Exception:
            pass

    # 3. pyproject.toml (PEP 621 or Poetry requires-python)
    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore")
            m_req = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
            if not m_req:
                m_req = re.search(r'python\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
            if m_req:
                req_str = m_req.group(1).strip()
                # Determine best match based on upper and lower bounds
                if "<3.13" in req_str or "< 3.13" in req_str:
                    return ("3.12", req_str, f"pyproject.toml requires {req_str} -> selected Python 3.12")
                if "<3.12" in req_str or "< 3.12" in req_str:
                    return ("3.11", req_str, f"pyproject.toml requires {req_str} -> selected Python 3.11")
                if "<3.11" in req_str or "< 3.11" in req_str:
                    return ("3.10", req_str, f"pyproject.toml requires {req_str} -> selected Python 3.10")
                if "3.13" in req_str and "<" not in req_str:
                    return ("3.13", req_str, f"pyproject.toml requires {req_str} -> selected Python 3.13")
                # If >=3.10 or >=3.11 or ^3.10, default to 3.12 (modern stable)
                return ("3.12", req_str, f"pyproject.toml requires {req_str} -> selected Python 3.12")
        except Exception:
            pass

    # 4. Existing Dockerfile inspection
    for df_path in [repo_path / "Dockerfile", repo_path / "docker" / "Dockerfile"]:
        if df_path.is_file():
            try:
                content = df_path.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"FROM\s+python:(\d+\.\d+)", content, re.IGNORECASE)
                if m:
                    ver = m.group(1)
                    for sup in ("3.13", "3.12", "3.11", "3.10"):
                        if ver.startswith(sup):
                            return (sup, f"FROM python:{ver}", f"Existing Dockerfile uses python:{ver} -> selected Python {sup}")
            except Exception:
                pass

    # 5. Default fallback to 3.12
    return ("3.12", ">=3.10", "Default stable Python runtime selected: 3.12")


def detect_package_manager(repo_path: Path) -> tuple[str, list[str], str]:
    """
    Detects repository package manager (uv, poetry, pipenv, pip) and
    constructs the optimal dependency installation command.
    Returns (manager, install_command, rationale).
    """
    # 1. uv
    if (repo_path / "uv.lock").is_file():
        return ("uv", ["uv", "pip", "install", "-r", "pyproject.toml"], "uv.lock detected -> using uv package manager")

    # 2. Poetry
    if (repo_path / "poetry.lock").is_file():
        return ("poetry", ["poetry", "install", "--no-root", "--no-interaction"], "poetry.lock detected -> using Poetry")

    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore")
            if "[tool.poetry]" in content:
                return ("poetry", ["poetry", "install", "--no-root", "--no-interaction"], "pyproject.toml with [tool.poetry] -> using Poetry")
            if "[tool.uv]" in content:
                return ("uv", ["uv", "pip", "install", "-r", "pyproject.toml"], "pyproject.toml with [tool.uv] -> using uv")
        except Exception:
            pass

    # 3. Pipfile (Pipenv)
    if (repo_path / "Pipfile").is_file():
        return ("pipenv", ["pipenv", "install", "--system", "--deploy"], "Pipfile detected -> using pipenv")

    # 4. Standard requirements.txt variants
    for req_candidate in ["requirements.txt", "requirements/production.txt", "requirements/prod.txt", "requirements/base.txt"]:
        if (repo_path / req_candidate).is_file():
            return ("pip", ["pip", "install", "--no-cache-dir", "-r", req_candidate], f"{req_candidate} detected -> using standard pip")

    # Fallback
    return ("pip", ["pip", "install", "--no-cache-dir", "-r", "requirements.txt"], "Standard pip dependency installation")


def detect_health_endpoint(repo_path: Path) -> tuple[str, str]:
    """
    Scans URL configurations and routing files for dedicated health endpoints.
    Returns (endpoint_path, rationale).
    """
    import re

    # Scan urls.py or routes.py
    for py_file in repo_path.glob("**/*urls*.py"):
        rel = py_file.relative_to(repo_path)
        if any(p.startswith(".") or p in {"venv", ".venv", "env", "node_modules"} for p in rel.parts):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            for ep in ["health/", "healthz", "api/health/", "ping/", "status/"]:
                if re.search(rf'["\']{re.escape(ep)}["\']', content):
                    clean_ep = "/" + ep.rstrip("/") + "/"
                    return (clean_ep, f"Dedicated health endpoint '{clean_ep}' detected in {rel}")
        except Exception:
            pass

    # Scan FastAPI / Flask main.py / app.py routes
    for candidate in ["main.py", "app.py", "src/main.py", "src/app.py"]:
        app_file = repo_path / candidate
        if app_file.is_file():
            try:
                content = app_file.read_text(encoding="utf-8", errors="ignore")
                for ep in ["/health", "/healthz", "/api/health", "/ping"]:
                    if f'"{ep}"' in content or f"'{ep}'" in content:
                        return (ep, f"Health route '{ep}' detected in {candidate}")
            except Exception:
                pass

    return ("/", "Default root '/' endpoint selected for health verification")


def analyze_env_contract(repo_path: Path) -> dict[str, Any]:
    """
    Analyzes codebase for environment variable requirements.
    Classifies variables into AUTO_GENERATED, INFRASTRUCTURE, OPTIONAL, and USER_REQUIRED.
    """
    import re

    vars_found: set[str] = set()

    # 1. Parse .env.example / .env.sample / example.env
    for env_name in [".env.example", ".env.sample", "example.env", ".env.template"]:
        env_file = repo_path / env_name
        if env_file.is_file():
            try:
                for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    clean = line.strip()
                    if clean and not clean.startswith("#") and "=" in clean:
                        var_name = clean.split("=", 1)[0].strip()
                        if var_name:
                            vars_found.add(var_name)
            except Exception:
                pass

    # 2. Parse os.getenv / os.environ.get / env(...) / config(...) in settings.py
    for py_file in repo_path.glob("**/settings*.py"):
        rel = py_file.relative_to(repo_path)
        if any(p.startswith(".") or p in {"venv", ".venv", "env"} for p in rel.parts):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            # Matches os.getenv("VAR") or os.environ.get("VAR") or env("VAR") or config("VAR")
            matches = re.findall(r'(?:os\.getenv|os\.environ\.get|env|config)\s*\(\s*["\']([A-Z0-9_]+)["\']', content)
            for m in matches:
                vars_found.add(m)
        except Exception:
            pass

    auto_generated = {
        "SECRET_KEY", "DATABASE_URL", "POSTGRES_PASSWORD", "POSTGRES_DB",
        "POSTGRES_USER", "POSTGRES_HOST", "POSTGRES_PORT", "REDIS_URL",
        "REDIS_PASSWORD", "CELERY_BROKER_URL",
    }
    infrastructure = {
        "DEBUG", "ALLOWED_HOSTS", "PORT", "CSRF_TRUSTED_ORIGINS",
        "STATIC_ROOT", "STATIC_URL", "MEDIA_ROOT", "MEDIA_URL", "ENVIRONMENT",
    }

    user_required: list[str] = []
    optional: list[str] = []

    for var in sorted(vars_found):
        if var in auto_generated or var in infrastructure:
            continue
        # If looks like API key, secret token, password, credential, client ID
        if any(s in var for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH", "CLIENT_ID", "PRIVATE")):
            user_required.append(var)
        else:
            optional.append(var)

    return {
        "auto_generated": sorted(list(vars_found & auto_generated)),
        "infrastructure": sorted(list(vars_found & infrastructure)),
        "user_required": user_required,
        "optional": optional,
    }


def extract_dependencies_from_repo(repo_path: Path) -> Set[str]:
    """
    Extracts all declared dependencies from requirements.txt files,
    pyproject.toml (poetry/flit/pep621), and Pipfile.
    """
    import re
    deps: Set[str] = set()
    req_files = list(repo_path.glob("requirements*.txt")) + list(repo_path.glob("requirements/*.txt"))
    for req_path in req_files:
        try:
            for line in req_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                clean = line.strip().split("#")[0].strip()
                if clean and not clean.startswith("-"):
                    clean_no_extras = re.sub(r"\[.*?\]", "", clean)
                    pkg = re.split(r"[><=~!@\s]", clean_no_extras)[0].lower()
                    if pkg:
                        deps.add(pkg)
        except Exception:
            pass

    for manifest_file in [repo_path / "pyproject.toml", repo_path / "Pipfile"]:
        if manifest_file.is_file():
            try:
                content = manifest_file.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'^\s*([a-zA-Z0-9_\-\.]+)\s*=', content, re.MULTILINE):
                    k = m.group(1).lower()
                    if k not in {"name", "version", "description", "authors", "python", "packages", "readme"}:
                        deps.add(k)
                for token in re.findall(r'["\']([^"\']+)["\']', content):
                    clean_token = re.sub(r"\[.*?\]", "", token).strip()
                    pkg = re.split(r"[><=~!^@\s,]", clean_token)[0].strip().lower()
                    if pkg and re.match(r"^[a-z0-9][a-z0-9_\-\.]*$", pkg):
                        deps.add(pkg)
            except Exception:
                pass

    return deps

