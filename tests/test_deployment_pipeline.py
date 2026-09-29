"""
Tests for Docker Deployment Pipeline, Incremental Updates, and Health Checks.
"""

from unittest.mock import MagicMock
import urllib.error
import pytest
from rich.console import Console

from deployx.config import paths, reload_paths
from deployx.core.command import CommandResult
from deployx.deployment.deploy import run_deployment
from deployx.deployment.health import perform_http_healthcheck
from deployx.deployment.project import add_project
from deployx.deployment.update import run_update
from deployx.detectors.base import DetectedInfrastructure, DetectionResult, FrameworkType
from deployx.models import DeploymentState, DeploymentStatus, HealthStatus
from deployx.state import get_state_manager


@pytest.fixture(autouse=True)
def isolated_deployx(tmp_path, monkeypatch, mocker):
    root = tmp_path / "opt_deployx"
    etc = tmp_path / "etc_deployx"
    monkeypatch.setenv("DEPLOYX_ROOT", str(root))
    monkeypatch.setenv("DEPLOYX_CONFIG_DIR", str(etc))
    reload_paths()
    paths.ensure_all_dirs()
    mocker.patch("deployx.deployment.project.verify_public_repository", return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f")
    yield


def test_deployment_pipeline_success(mocker):
    # 1. Register project
    console = Console(record=True)
    add_project(
        name="store-app",
        git="https://github.com/acme-org/store.git",
        branch="main",
        private=False,
        domain="store.example.com",
        framework="django",
        database="postgres",
        console=console,
    )

    # 2. Mock Git sync
    mocker.patch(
        "deployx.deployment.deploy.GitRepositoryManager.sync_repository",
        return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f",
    )

    # 3. Mock framework detection
    infra = DetectedInfrastructure(
        has_dockerfile=False,
        has_postgres=True,
        has_redis=False,
        wsgi_module="store.wsgi:application",
    )
    mock_detection = DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=infra,
    )
    mocker.patch("deployx.deployment.deploy.detect_repository", return_value=mock_detection)

    # 4. Mock Docker Compose commands
    mock_cmd = CommandResult(command=["docker"], returncode=0, stdout="", stderr="", duration=0.1)
    mocker.patch("deployx.docker.compose.DockerComposeManager.build", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.up", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.run_transient", return_value=mock_cmd)

    # 5. Mock Health check
    mocker.patch("deployx.deployment.deploy.perform_http_healthcheck", return_value=True)

    # Execute deployment
    success = run_deployment("store-app", console=console)
    assert success is True

    # Verify state was saved
    state = get_state_manager().get_state("store-app")
    assert state is not None
    assert state.current_commit == "f0222384a29ebcd90d80111bfbc7c1abf8da4e0f"
    assert state.status == DeploymentStatus.HEALTHY
    assert state.health_status == HealthStatus.HEALTHY
    assert state.docker_image == "deployx_store-app:f022238"


def test_update_already_up_to_date(mocker):
    console = Console(record=True)
    add_project(
        name="web-blog",
        git="https://github.com/acme-org/blog.git",
        branch="main",
        private=False,
        domain=None,
        framework="django",
        database="postgres",
        console=console,
    )

    # Set state as already on commit 4ba128c
    state_mgr = get_state_manager()
    initial_state = DeploymentState.new("web-blog", "https://github.com/acme-org/blog.git", "main")
    initial_state.current_commit = "4ba128c704f056d61f1cf01bfbc7c1abf8da4e0f"
    initial_state.status = DeploymentStatus.HEALTHY
    state_mgr.save_state(initial_state)

    # Remote returns same commit
    mocker.patch(
        "deployx.git.repository.GitRepositoryManager.get_remote_commit",
        return_value="4ba128c704f056d61f1cf01bfbc7c1abf8da4e0f",
    )
    deploy_spy = mocker.patch("deployx.deployment.update.run_deployment")

    res = run_update("web-blog", console=console)
    assert res is True
    # Deployment should NOT be triggered
    deploy_spy.assert_not_called()
    assert "Already up to date" in console.export_text()


def test_update_triggers_new_deployment(mocker):
    console = Console(record=True)
    add_project(
        name="web-news",
        git="https://github.com/acme-org/news.git",
        branch="main",
        private=False,
        domain=None,
        framework="django",
        database="postgres",
        console=console,
    )

    # State has old commit
    state_mgr = get_state_manager()
    old_state = DeploymentState.new("web-news", "https://github.com/acme-org/news.git", "main")
    old_state.current_commit = "f0222384a29ebcd90d80111bfbc7c1abf8da4e0f"
    state_mgr.save_state(old_state)

    # Remote returns new commit
    new_sha = "4ba128c704f056d61f1cf01bfbc7c1abf8da4e0f"
    mocker.patch(
        "deployx.git.repository.GitRepositoryManager.get_remote_commit",
        return_value=new_sha,
    )
    deploy_mock = mocker.patch("deployx.deployment.update.run_deployment", return_value=True)

    res = run_update("web-news", console=console)
    assert res is True
    deploy_mock.assert_called_once_with("web-news", target_commit=new_sha, console=console)


def test_healthcheck_polling_success(mocker):
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.__enter__.return_value = mock_resp

    mocker.patch("urllib.request.urlopen", return_value=mock_resp)

    ok = perform_http_healthcheck(port=8000, path="/", retries=2, interval=0)
    assert ok is True


def test_healthcheck_polling_failure(mocker):
    mocker.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused"))
    ok = perform_http_healthcheck(port=8000, path="/", retries=2, interval=0)
    assert ok is False
