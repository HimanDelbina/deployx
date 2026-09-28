"""
Tests for Project Registration, State Persistence, and CLI Commands.
"""

from pathlib import Path
import pytest
from typer.testing import CliRunner

from deployx.cli import app
from deployx.config import paths, reload_paths
from deployx.deployment.project import load_project_config, project_exists
from deployx.models import DeploymentStatus, HealthStatus
from deployx.state import JsonFileStateBackend, get_state_manager

runner = CliRunner()


@pytest.fixture(autouse=True)
def setup_isolated_env(tmp_path, monkeypatch):
    """Configures isolated test root for each test run."""
    root_dir = tmp_path / "deployx_root"
    config_dir = tmp_path / "deployx_etc"
    monkeypatch.setenv("DEPLOYX_ROOT", str(root_dir))
    monkeypatch.setenv("DEPLOYX_CONFIG_DIR", str(config_dir))
    reload_paths()
    paths.ensure_all_dirs()
    yield


def test_state_backend_save_and_get():
    state_mgr = get_state_manager()
    from deployx.models import DeploymentState

    state = DeploymentState.new("testproj", "git@github.com:foo/bar.git", "main")
    state.current_commit = "1234567"
    state.status = DeploymentStatus.HEALTHY
    state.health_status = HealthStatus.HEALTHY

    state_mgr.save_state(state)

    loaded = state_mgr.get_state("testproj")
    assert loaded is not None
    assert loaded.project == "testproj"
    assert loaded.current_commit == "1234567"
    assert loaded.status == DeploymentStatus.HEALTHY


def test_cli_project_add_and_info():
    # Register a new project
    res = runner.invoke(
        app,
        [
            "project", "add",
            "--name", "blog-app",
            "--git", "https://github.com/example/blog.git",
            "--branch", "main",
            "--framework", "django",
            "--database", "postgres",
        ],
    )
    assert res.exit_code == 0
    assert "registered successfully" in res.stdout
    assert project_exists("blog-app")

    cfg = load_project_config("blog-app")
    assert cfg.project.name == "blog-app"
    assert cfg.git.repository == "https://github.com/example/blog.git"
    assert cfg.git.private is False

    # Check project info
    info_res = runner.invoke(app, ["project", "info", "blog-app"])
    assert info_res.exit_code == 0
    assert "blog-app" in info_res.stdout
    assert "https://github.com/example/blog.git" in info_res.stdout


def test_cli_project_add_duplicate():
    # Add first time
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "demo",
            "--git", "https://github.com/example/demo.git",
        ],
    )
    # Add second time should fail
    res2 = runner.invoke(
        app,
        [
            "project", "add",
            "--name", "demo",
            "--git", "https://github.com/example/demo.git",
        ],
    )
    assert res2.exit_code == 1
    assert "already exists" in res2.stdout


def test_cli_project_list():
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "proj-a",
            "--git", "https://github.com/example/a.git",
        ],
    )
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "proj-b",
            "--git", "git@github.com:example/b.git",
            "--private",
        ],
    )

    list_res = runner.invoke(app, ["project", "list"])
    assert list_res.exit_code == 0
    assert "proj-a" in list_res.stdout
    assert "proj-b" in list_res.stdout
