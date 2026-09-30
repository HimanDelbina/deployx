"""
DeployX v0.2.0 Detection & Generator Test Suite
Validates autonomous detection for FastAPI, Flask, Generic Python,
runtime Python versions, package managers (pip, poetry, uv),
environment contracts, and multi-framework Dockerfile/Compose generators.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from deployx.detectors.base import (
    analyze_env_contract,
    detect_health_endpoint,
    detect_package_manager,
    detect_python_version,
)
from deployx.detectors.django import DjangoDetector
from deployx.detectors.fastapi import FastAPIDetector
from deployx.detectors.flask import FlaskDetector
from deployx.detectors.generic_python import GenericPythonDetector
from deployx.detectors.registry import detect_repository
from deployx.generators.registry import (
    generate_compose_for_project,
    generate_dockerfile_for_project,
)
from deployx.models import (
    BuildConfig,
    DatabasePreference,
    DeploymentConfig,
    DockerConfig,
    FrameworkType,
    GitConfig,
    HealthcheckConfig,
    ProjectConfig,
    ProjectMeta,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "sample_app"
    repo.mkdir(parents=True, exist_ok=True)
    return repo


# ==============================================================================
# 1. FastAPI Detection Tests
# ==============================================================================
def test_fastapi_detection(temp_repo: Path):
    (temp_repo / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health(): return {'status': 'ok'}",
        encoding="utf-8",
    )
    (temp_repo / "requirements.txt").write_text(
        "fastapi>=0.100.0\nuvicorn>=0.23.0\nasyncpg>=0.28.0\n",
        encoding="utf-8",
    )
    (temp_repo / ".python-version").write_text("3.11.8\n", encoding="utf-8")
    (temp_repo / ".env.example").write_text("DATABASE_URL=postgres://...\nSECRET_KEY=\n", encoding="utf-8")

    result = detect_repository(temp_repo)
    assert result.framework == FrameworkType.FASTAPI
    assert result.confidence >= 0.8
    assert result.infrastructure.selected_python_version == "3.11"
    assert result.infrastructure.package_manager == "pip"
    assert result.infrastructure.has_postgres is True
    assert result.infrastructure.asgi_module == "main:app"
    assert result.infrastructure.health_endpoint == "/health"
    all_env_vars = (
        result.infrastructure.env_contract.get("auto_generated", [])
        + result.infrastructure.env_contract.get("user_required", [])
    )
    assert "SECRET_KEY" in all_env_vars
    assert "framework" in result.explanation


# ==============================================================================
# 2. Flask Detection Tests
# ==============================================================================
def test_flask_detection(temp_repo: Path):
    (temp_repo / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n@app.route('/api/health')\ndef h(): return 'ok'",
        encoding="utf-8",
    )
    (temp_repo / "pyproject.toml").write_text(
        """[tool.poetry]
name = "flask-service"
version = "0.1.0"
[tool.poetry.dependencies]
python = "^3.12"
flask = "^3.0.0"
gunicorn = "^21.0.0"
psycopg2-binary = "^2.9.0"
""",
        encoding="utf-8",
    )
    (temp_repo / "poetry.lock").write_text("# lockfile\n", encoding="utf-8")

    result = detect_repository(temp_repo)
    assert result.framework == FrameworkType.FLASK
    assert result.confidence >= 0.8
    assert result.infrastructure.package_manager == "poetry"
    assert result.infrastructure.has_postgres is True
    assert result.infrastructure.wsgi_module == "app:app"
    assert result.infrastructure.health_endpoint == "/api/health"


# ==============================================================================
# 3. Generic Python Fallback Detection Tests
# ==============================================================================
def test_generic_python_detection(temp_repo: Path):
    (temp_repo / "main.py").write_text("import time\nprint('Running script...')\n", encoding="utf-8")
    (temp_repo / "requirements.txt").write_text("requests>=2.31.0\npydantic>=2.5.0\n", encoding="utf-8")

    result = detect_repository(temp_repo)
    assert result.framework in (FrameworkType.GENERIC_PYTHON, FrameworkType.CUSTOM)
    assert result.infrastructure.package_manager == "pip"
    assert result.infrastructure.selected_python_version == "3.12"


# ==============================================================================
# 4. Runtime Python Version Discovery Tests
# ==============================================================================
def test_detect_python_version_precedence(temp_repo: Path):
    # runtime.txt
    (temp_repo / "runtime.txt").write_text("python-3.11.4\n", encoding="utf-8")
    ver, raw, reason = detect_python_version(temp_repo)
    assert ver == "3.11"
    assert "runtime.txt" in reason

    # .python-version overrides or detected
    (temp_repo / "runtime.txt").unlink()
    (temp_repo / ".python-version").write_text("3.10.12\n", encoding="utf-8")
    ver, raw, reason = detect_python_version(temp_repo)
    assert ver == "3.10"
    assert ".python-version" in reason

    # pyproject.toml requires-python
    (temp_repo / ".python-version").unlink()
    (temp_repo / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.9, <3.13"\n',
        encoding="utf-8",
    )
    ver, raw, reason = detect_python_version(temp_repo)
    assert ver == "3.12"
    assert "pyproject.toml" in reason


# ==============================================================================
# 5. Package Manager Detection Tests (pip, poetry, uv)
# ==============================================================================
def test_detect_package_manager(temp_repo: Path):
    # UV lockfile
    (temp_repo / "uv.lock").write_text("# uv\n", encoding="utf-8")
    mgr, cmd, reason = detect_package_manager(temp_repo)
    assert mgr == "uv"
    assert "uv.lock" in reason

    # Poetry lockfile
    (temp_repo / "uv.lock").unlink()
    (temp_repo / "poetry.lock").write_text("# poetry\n", encoding="utf-8")
    mgr, cmd, reason = detect_package_manager(temp_repo)
    assert mgr == "poetry"
    assert "poetry.lock" in reason

    # Pip fallback
    (temp_repo / "poetry.lock").unlink()
    (temp_repo / "requirements.txt").write_text("django>=5.0\n", encoding="utf-8")
    mgr, cmd, reason = detect_package_manager(temp_repo)
    assert mgr == "pip"
    assert "requirements.txt" in reason


# ==============================================================================
# 6. Environment Contract Analysis Tests
# ==============================================================================
def test_analyze_env_contract(temp_repo: Path):
    (temp_repo / ".env.example").write_text(
        "DATABASE_URL=\nAPI_KEY=\nOPTIONAL_PORT=8000\n",
        encoding="utf-8",
    )
    contract = analyze_env_contract(temp_repo)
    assert "DATABASE_URL" in contract["auto_generated"]
    assert "API_KEY" in contract["user_required"]
    assert "OPTIONAL_PORT" in contract["optional"]


# ==============================================================================
# 7. Unified Dockerfile and Compose Generators Tests
# ==============================================================================
def test_fastapi_generator(temp_repo: Path, tmp_path: Path):
    (temp_repo / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (temp_repo / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")

    det = detect_repository(temp_repo)
    cfg = ProjectConfig(
        project=ProjectMeta(name="fastapi-test"),
        git=GitConfig(repository="https://github.com/foo/bar.git", branch="main"),
        deployment=DeploymentConfig(
            framework=FrameworkType.FASTAPI,
            database=DatabasePreference.POSTGRES,
            docker=DockerConfig(port=8050),
        ),
    )

    df_path = tmp_path / "Dockerfile.deployx"
    compose_path = tmp_path / "docker-compose.deployx.yml"

    generate_dockerfile_for_project(cfg, det, df_path)
    generate_compose_for_project(cfg, det, "deployx_fastapi-test:latest", compose_path)

    df_content = df_path.read_text(encoding="utf-8")
    assert "FROM python:3.12-slim-bookworm" in df_content
    assert "uvicorn" in df_content
    assert "main:app" in df_content

    compose_content = compose_path.read_text(encoding="utf-8")
    assert "8050:8000" in compose_content
    assert "deployx_fastapi-test_pgdata" in compose_content
    assert "postgres:16-alpine" in compose_content


def test_flask_generator(temp_repo: Path, tmp_path: Path):
    (temp_repo / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8")
    (temp_repo / "requirements.txt").write_text("flask\ngunicorn\n", encoding="utf-8")

    det = detect_repository(temp_repo)
    cfg = ProjectConfig(
        project=ProjectMeta(name="flask-test"),
        git=GitConfig(repository="https://github.com/foo/bar.git", branch="main"),
        deployment=DeploymentConfig(
            framework=FrameworkType.FLASK,
            database=DatabasePreference.NONE,
            docker=DockerConfig(port=8080),
        ),
    )

    df_path = tmp_path / "Dockerfile.deployx"
    compose_path = tmp_path / "docker-compose.deployx.yml"

    generate_dockerfile_for_project(cfg, det, df_path)
    generate_compose_for_project(cfg, det, "deployx_flask-test:latest", compose_path)

    df_content = df_path.read_text(encoding="utf-8")
    assert "FROM python:3.12-slim-bookworm" in df_content
    assert "gunicorn" in df_content
    assert "app:app" in df_content

    compose_content = compose_path.read_text(encoding="utf-8")
    assert "8080:8000" in compose_content
    assert "db:" not in compose_content
