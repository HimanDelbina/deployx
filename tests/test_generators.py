"""
Tests for Django Dockerfile, Compose, and Environment Generators.
"""

from pathlib import Path
import pytest
import yaml

from deployx.config import paths, reload_paths
from deployx.detectors.base import DetectedInfrastructure, DetectionResult
from deployx.generators.django import generate_compose_file, generate_django_dockerfile
from deployx.generators.environment import generate_production_env, parse_env_example
from deployx.models import (
    DatabasePreference,
    DeploymentConfig,
    FrameworkType,
    GitConfig,
    ProjectConfig,
    ProjectMeta,
)


@pytest.fixture(autouse=True)
def isolated_deployx(tmp_path, monkeypatch):
    root = tmp_path / "opt_deployx"
    etc = tmp_path / "etc_deployx"
    monkeypatch.setenv("DEPLOYX_ROOT", str(root))
    monkeypatch.setenv("DEPLOYX_CONFIG_DIR", str(etc))
    reload_paths()
    paths.ensure_all_dirs()
    yield


def test_generate_production_env(tmp_path):
    pname = "sample-proj"
    project_dir = paths.get_project_dir(pname)
    project_dir.mkdir(parents=True)

    example_env = tmp_path / ".env.example"
    example_env.write_text("CUSTOM_API_KEY=dummy\nFEATURE_FLAG=true\n")

    cfg = ProjectConfig(
        version=1,
        project=ProjectMeta(name=pname),
        git=GitConfig(repository="https://github.com/acme-org/mybackend.git"),
        deployment=DeploymentConfig(
            framework=FrameworkType.DJANGO,
            database=DatabasePreference.POSTGRES,
            redis=True,
            domain="app.example.com",
        ),
    )

    env_path = generate_production_env(cfg, example_env_path=example_env)
    assert env_path.is_file()

    content = env_path.read_text()
    assert "DEBUG=\"False\"" in content
    assert "SECRET_KEY=" in content
    assert "POSTGRES_PASSWORD=" in content
    assert "REDIS_URL=" in content
    assert "CUSTOM_API_KEY=" in content
    assert "FEATURE_FLAG=\"true\"" in content

    # Test non-overwrite
    prev_content = content
    env_path2 = generate_production_env(cfg, overwrite=False)
    assert env_path2.read_text() == prev_content


def test_generate_compose_and_dockerfile(tmp_path):
    pname = "django-web"
    project_dir = paths.get_project_dir(pname)
    project_dir.mkdir(parents=True)

    cfg = ProjectConfig(
        version=1,
        project=ProjectMeta(name=pname),
        git=GitConfig(repository="https://github.com/acme-org/web.git"),
        deployment=DeploymentConfig(
            framework=FrameworkType.DJANGO,
            database=DatabasePreference.POSTGRES,
            redis=True,
        ),
    )

    infra = DetectedInfrastructure(
        has_dockerfile=False,
        has_postgres=True,
        has_redis=True,
        wsgi_module="myapp.wsgi:application",
    )
    detection = DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=infra,
    )

    # 1. Generate Dockerfile
    dockerfile_path = project_dir / "Dockerfile.deployx"
    generate_django_dockerfile(cfg, detection, dockerfile_path)
    assert dockerfile_path.is_file()
    df_content = dockerfile_path.read_text()
    assert "FROM python:3.12-slim-bookworm" in df_content
    assert "myapp.wsgi:application" in df_content

    # 2. Generate Compose file
    compose_path = project_dir / "docker-compose.deployx.yml"
    generate_compose_file(cfg, detection, "django-web:abc1234", compose_path)
    assert compose_path.is_file()

    compose_data = yaml.safe_load(compose_path.read_text())
    assert compose_data["name"] == "deployx_django-web"
    assert "web" in compose_data["services"]
    assert "db" in compose_data["services"]
    assert "redis" in compose_data["services"]
    assert compose_data["services"]["web"]["image"] == "django-web:abc1234"
    assert compose_data["services"]["web"]["container_name"] == "deployx_django-web_web"
