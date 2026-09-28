"""
Tests for Django Detector and Framework Detection Registry.
"""

from pathlib import Path
from deployx.detectors.base import FrameworkType
from deployx.detectors.django import DjangoDetector
from deployx.detectors.registry import detect_repository


def test_django_detector_full(tmp_path):
    repo = tmp_path / "my_django_project"
    repo.mkdir()

    # Create manage.py
    (repo / "manage.py").write_text("#!/usr/bin/env python\nimport os\n# django manage.py\n")

    # Create app/wsgi.py
    app_dir = repo / "my_app"
    app_dir.mkdir()
    (app_dir / "wsgi.py").write_text("# wsgi app")
    (app_dir / "settings.py").write_text("DEBUG = False\n")

    # Create requirements.txt
    (repo / "requirements.txt").write_text(
        "Django>=5.0\n"
        "psycopg2-binary==2.9.9\n"
        "redis==5.0.1\n"
        "celery==5.3.6\n"
        "gunicorn==21.2.0\n"
    )

    # Existing infrastructure
    (repo / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (repo / "docker-compose.yml").write_text("version: '3.8'\nservices:\n  web:\n    build: .\n")
    (repo / ".env.example").write_text("SECRET_KEY=example\nDEBUG=True\n")

    res = detect_repository(repo)
    assert res.framework == FrameworkType.DJANGO
    assert res.is_confident
    assert res.confidence >= 0.8
    assert "manage.py" in res.matched_indicators

    infra = res.infrastructure
    assert infra.has_dockerfile is True
    assert infra.has_compose is True
    assert infra.has_env_example is True
    assert infra.has_postgres is True
    assert infra.has_redis is True
    assert infra.has_celery is True
    assert infra.has_gunicorn is True
    assert infra.wsgi_module == "my_app.wsgi:application"


def test_django_detector_pyproject_toml(tmp_path):
    repo = tmp_path / "pyproject_django"
    repo.mkdir()

    (repo / "manage.py").write_text("import django")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "web"\ndependencies = ["django>=4.2", "psycopg[binary]>=3.1"]\n'
    )

    detector = DjangoDetector()
    res = detector.detect(repo)
    assert res.framework == FrameworkType.DJANGO
    assert res.is_confident
    assert res.infrastructure.has_postgres is True
    assert res.infrastructure.has_dockerfile is False


def test_non_django_repo(tmp_path):
    repo = tmp_path / "random_project"
    repo.mkdir()
    (repo / "index.js").write_text("console.log('hello')")

    res = detect_repository(repo)
    assert not res.is_confident
    assert res.confidence == 0.0
