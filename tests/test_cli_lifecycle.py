"""
Tests for Lifecycle CLI commands: status, logs, restart, stop, start.
"""

from unittest.mock import MagicMock
import pytest
from typer.testing import CliRunner

from deployx.cli import app
from deployx.config import paths, reload_paths
from deployx.core.command import CommandResult
from deployx.deployment.project import add_project
from deployx.models import DeploymentState, DeploymentStatus, HealthStatus
from deployx.state import get_state_manager

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_deployx(tmp_path, monkeypatch):
    root = tmp_path / "opt_deployx"
    etc = tmp_path / "etc_deployx"
    monkeypatch.setenv("DEPLOYX_ROOT", str(root))
    monkeypatch.setenv("DEPLOYX_CONFIG_DIR", str(etc))
    reload_paths()
    paths.ensure_all_dirs()
    yield


def test_cli_status(mocker):
    # Register project
    from rich.console import Console
    add_project(
        name="status-app",
        git="https://github.com/example/status.git",
        branch="main",
        private=False,
        domain=None,
        framework="django",
        database="postgres",
        console=Console(record=True),
    )

    # Save state
    state = DeploymentState.new("status-app", "https://github.com/example/status.git", "main")
    state.status = DeploymentStatus.HEALTHY
    state.health_status = HealthStatus.HEALTHY
    state.current_commit = "f0222384a29ebcd90d80111bfbc7c1abf8da4e0f"
    get_state_manager().save_state(state)

    mocker.patch(
        "deployx.docker.compose.DockerComposeManager.ps",
        return_value=CommandResult(
            command=["docker", "compose", "ps"],
            returncode=0,
            stdout="deployx_status-app_web   Up 2 hours   0.0.0.0:8000->8000/tcp\n",
            stderr="",
            duration=0.1,
        ),
    )

    # Invoke status command
    res = runner.invoke(app, ["status", "status-app"])
    assert res.exit_code == 0
    assert "DeployX Status: status-app" in res.stdout
    assert "healthy" in res.stdout
    assert "f0222384a29ebcd90d80111bfbc7c1abf8da4e0f" in res.stdout


def test_cli_lifecycle_commands(mocker):
    from rich.console import Console
    add_project(
        name="ctrl-app",
        git="https://github.com/example/ctrl.git",
        branch="main",
        private=False,
        domain=None,
        framework="django",
        database="postgres",
        console=Console(record=True),
    )

    mock_cmd = CommandResult(command=["docker"], returncode=0, stdout="", stderr="", duration=0.1)
    mock_restart = mocker.patch("deployx.docker.compose.DockerComposeManager.restart", return_value=mock_cmd)
    mock_stop = mocker.patch("deployx.docker.compose.DockerComposeManager.stop", return_value=mock_cmd)
    mock_start = mocker.patch("deployx.docker.compose.DockerComposeManager.start", return_value=mock_cmd)

    # Test restart
    res_r = runner.invoke(app, ["restart", "ctrl-app"])
    assert res_r.exit_code == 0
    mock_restart.assert_called_once()

    # Test stop
    res_s = runner.invoke(app, ["stop", "ctrl-app"])
    assert res_s.exit_code == 0
    mock_stop.assert_called_once()

    # Test start
    res_st = runner.invoke(app, ["start", "ctrl-app"])
    assert res_st.exit_code == 0
    mock_start.assert_called_once()


def test_cli_logs(mocker):
    from rich.console import Console
    add_project(
        name="log-app",
        git="https://github.com/example/log.git",
        branch="main",
        private=False,
        domain=None,
        framework="django",
        database="postgres",
        console=Console(record=True),
    )

    # Create dummy compose file
    compose_path = paths.get_project_dir("log-app") / "docker-compose.deployx.yml"
    compose_path.write_text("version: '3.8'\n")

    mocker.patch(
        "deployx.docker.compose.DockerComposeManager.logs",
        return_value=CommandResult(
            command=["docker", "compose", "logs"],
            returncode=0,
            stdout="Starting gunicorn 21.2.0 on 0.0.0.0:8000\nBooting worker with pid: 14\n",
            stderr="",
            duration=0.1,
        ),
    )

    res = runner.invoke(app, ["logs", "log-app"])
    assert res.exit_code == 0
    assert "Starting gunicorn" in res.stdout
