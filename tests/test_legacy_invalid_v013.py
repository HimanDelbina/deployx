"""
Comprehensive Test Suite for DeployX v0.1.3:
- Safe handling of invalid/legacy project configurations
- INVALID_CONFIG status in 'project list'
- Non-crashing diagnostic panel in 'project info'
- Safe project removal preserving keys, backups, and volumes
- Project configuration repair via 'project edit'
- Clean blockage of 'deploy', 'update', and 'key create' without tracebacks
- Unprivileged user PermissionError handling with 'sudo deployx' guidance
- Zero raw Pydantic tracebacks on CLI commands
"""

from pathlib import Path
import pytest
from typer.testing import CliRunner

from deployx.cli import app
from deployx.config import paths, reload_paths
from deployx.deployment.project import load_project_config, load_project_raw


runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_deployx(tmp_path, monkeypatch):
    """Isolates DeployX directory tree for every test."""
    root = tmp_path / "opt_deployx"
    etc = tmp_path / "etc_deployx"
    monkeypatch.setenv("DEPLOYX_ROOT", str(root))
    monkeypatch.setenv("DEPLOYX_CONFIG_DIR", str(etc))
    reload_paths()
    paths.ensure_all_dirs()
    yield


def _create_legacy_invalid_project(project_name: str = "myapp") -> Path:
    """Helper to create a legacy v0.1.0 project with a placeholder URL."""
    pdir = paths.get_project_dir(project_name)
    pdir.mkdir(parents=True, exist_ok=True)
    bad_cfg = (
        "version: 1\n"
        f"project:\n  name: {project_name}\n"
        "git:\n  repository: git@github.com:HimanDelbina/REAL_REPOSITORY.git\n  branch: main\n  private: true\n"
        "deployment:\n  framework: django\n  database: postgres\n  docker:\n    compose_file: docker-compose.deployx.yml\n    port: 8000\n"
    )
    cfg_file = pdir / "deployx.yml"
    cfg_file.write_text(bad_cfg, encoding="utf-8")
    return cfg_file


# ==============================================================================
# 1. Project List with Invalid Config
# ==============================================================================
def test_project_list_shows_invalid_config_project():
    _create_legacy_invalid_project("myapp")
    res = runner.invoke(app, ["project", "list"])
    assert res.exit_code == 0
    assert "myapp" in res.output
    assert "INVALID_CONFIG" in res.output
    assert "No projects registered yet." not in res.output


# ==============================================================================
# 2. Project Info with Invalid Config
# ==============================================================================
def test_project_info_invalid_config_no_traceback():
    _create_legacy_invalid_project("myapp")
    res = runner.invoke(app, ["project", "info", "myapp"])
    assert res.exit_code == 0
    assert "Traceback (most recent call last):" not in res.output
    assert "pydantic_core" not in res.output
    assert "INVALID_CONFIG" in res.output
    assert "Actionable advice" in res.output
    assert "deployx project edit myapp" in res.output
    assert "deployx project remove myapp" in res.output


# ==============================================================================
# 3. Project Remove with Invalid Config
# ==============================================================================
def test_project_remove_invalid_config_succeeds():
    _create_legacy_invalid_project("brokenapp")
    pdir = paths.get_project_dir("brokenapp")
    assert pdir.exists()

    res = runner.invoke(app, ["project", "remove", "brokenapp", "--force"])
    assert res.exit_code == 0
    assert "Traceback (most recent call last):" not in res.output
    assert "successfully removed" in res.output
    assert not pdir.exists()


def test_project_remove_invalid_config_preserves_keys():
    _create_legacy_invalid_project("brokenapp")
    key_path = paths.get_project_key_path("brokenapp")
    pub_path = paths.get_project_pubkey_path("brokenapp")
    key_path.write_text("DUMMY_PRIVATE_KEY", encoding="utf-8")
    pub_path.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 DUMMY", encoding="utf-8")

    res = runner.invoke(app, ["project", "remove", "brokenapp", "--force"])
    assert res.exit_code == 0
    assert key_path.exists()
    assert pub_path.exists()
    assert "Deploy key preserved at:" in res.output


def test_project_remove_invalid_config_preserves_backups():
    _create_legacy_invalid_project("brokenapp")
    backup_file = paths.backups_dir / "brokenapp_20260929_120000.tar.gz"
    backup_file.write_text("BACKUP_DATA", encoding="utf-8")

    res = runner.invoke(app, ["project", "remove", "brokenapp", "--force"])
    assert res.exit_code == 0
    assert backup_file.exists()
    assert "Project backups preserved in" in res.output


def test_project_remove_invalid_config_preserves_volumes(mocker):
    _create_legacy_invalid_project("brokenapp")
    # Default remove does not call docker compose down with volumes
    mock_down = mocker.patch("deployx.docker.compose.DockerComposeManager.down")

    res = runner.invoke(app, ["project", "remove", "brokenapp", "--force"])
    assert res.exit_code == 0
    mock_down.assert_not_called()


# ==============================================================================
# 4. Project Edit Repairs Invalid Repository
# ==============================================================================
def test_project_edit_can_repair_invalid_repository(mocker):
    _create_legacy_invalid_project("myapp")

    # load_project_raw succeeds while load_project_config fails
    raw = load_project_raw("myapp")
    assert raw["git"]["repository"] == "git@github.com:HimanDelbina/REAL_REPOSITORY.git"
    with pytest.raises(Exception):
        load_project_config("myapp")

    mocker.patch(
        "deployx.deployment.project.verify_public_repository",
        return_value="commit1234567890abcdef1234567890abcdef12",
    )

    res = runner.invoke(
        app,
        [
            "project", "edit", "myapp",
            "--git", "https://github.com/myorg/repaired-repo.git",
            "--public",
            "--yes",
        ],
    )
    assert res.exit_code == 0
    assert "Traceback (most recent call last):" not in res.output
    assert "configuration updated successfully" in res.output.lower()

    # Verify repaired configuration loads and validates properly now
    cfg = load_project_config("myapp")
    assert cfg.git.repository == "https://github.com/myorg/repaired-repo.git"
    assert cfg.git.private is False


# ==============================================================================
# 5. Guarded Commands Against Invalid Config
# ==============================================================================
def test_deploy_invalid_config_is_blocked_cleanly():
    _create_legacy_invalid_project("brokenapp")
    res = runner.invoke(app, ["deploy", "brokenapp"])
    assert res.exit_code != 0
    assert "Traceback (most recent call last):" not in res.output
    assert "Deployment Blocked" in res.output
    assert "deployx project edit brokenapp" in res.output


def test_update_invalid_config_is_blocked_cleanly():
    _create_legacy_invalid_project("brokenapp")
    res = runner.invoke(app, ["update", "brokenapp"])
    assert res.exit_code != 0
    assert "Traceback (most recent call last):" not in res.output
    assert "Update Blocked" in res.output
    assert "deployx project edit brokenapp" in res.output


def test_key_create_invalid_config_clean_error():
    _create_legacy_invalid_project("brokenapp")
    res = runner.invoke(app, ["key", "create", "brokenapp"])
    assert res.exit_code != 0
    assert "Traceback (most recent call last):" not in res.output
    assert "Project repository configuration is invalid" in res.output


# ==============================================================================
# 6. Unprivileged User PermissionError Handling
# ==============================================================================
def test_unprivileged_project_list_clean_permission_error(mocker):
    mocker.patch(
        "deployx.deployment.project.list_projects",
        side_effect=PermissionError(13, "Permission denied", "/opt/deployx/projects"),
    )
    res = runner.invoke(app, ["project", "list"])
    assert res.exit_code == 1
    assert "Traceback (most recent call last):" not in res.output
    assert "Permission Denied" in res.output
    assert "sudo deployx project list" in res.output


def test_unprivileged_project_info_clean_permission_error(mocker):
    _create_legacy_invalid_project("myapp")
    mocker.patch(
        "deployx.deployment.project.inspect_project_config",
        side_effect=PermissionError(13, "Permission denied", "/opt/deployx/projects/myapp"),
    )
    res = runner.invoke(app, ["project", "info", "myapp"])
    assert res.exit_code == 1
    assert "Traceback (most recent call last):" not in res.output
    assert "Permission Denied" in res.output
    assert "sudo deployx project info myapp" in res.output


# ==============================================================================
# 7. Elimination of Raw Pydantic Traceback Across CLI
# ==============================================================================
def test_no_raw_pydantic_traceback_for_expected_cli_errors():
    _create_legacy_invalid_project("myapp")
    for cmd in [
        ["project", "list"],
        ["project", "info", "myapp"],
        ["key", "create", "myapp"],
        ["key", "verify", "myapp"],
        ["deploy", "myapp"],
        ["update", "myapp"],
    ]:
        res = runner.invoke(app, cmd)
        assert "Traceback (most recent call last):" not in res.output, f"Failed on command {cmd}"
        assert "pydantic_core._pydantic_core.ValidationError" not in res.output, f"Failed on command {cmd}"
