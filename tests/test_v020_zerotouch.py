"""
DeployX v0.2.0 Zero-Touch Deployment & Management Test Suite
Tests autonomous deployment via Git URL, port conflict auto-resolution,
mirror failover, dry-run, explain, auto-rollback, domain proxy,
timeline events, deep inspection, global & project config CLI,
init wizard, cleanup, and self-update engine.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from rich.console import Console
from typer.testing import CliRunner

from deployx.cli import app
from deployx.cleanup import run_system_cleanup
from deployx.config import (
    load_global_config,
    paths,
    reset_global_config,
    set_global_config_value,
    set_project_config_value,
    unset_global_config_value,
    unset_project_config_value,
)
from deployx.core.command import CommandError, CommandResult
from deployx.deployment.deploy import extract_project_name_from_url, is_git_url, run_deployment
from deployx.deployment.project import (
    add_project,
    get_project_timeline,
    inspect_project_details,
    load_project_config,
    project_exists,
)
from deployx.detectors.base import DetectedInfrastructure, DetectionResult
from deployx.generators.environment import prompt_for_required_secrets
from deployx.init_wizard import run_init_wizard
from deployx.models import (
    DatabasePreference,
    DeploymentConfig,
    DeploymentState,
    DeploymentStatus,
    DockerConfig,
    FrameworkType,
    HealthStatus,
    ProjectConfig,
    ProjectMeta,
)
from deployx.network.mirrors import (
    MirrorFailoverManager,
    auto_select_best_mirror,
    test_mirror as probe_mirror,
)
from deployx.network.ports import find_available_port, is_port_in_use
from deployx.network.proxy import ProxyManager
from deployx.self_update import SelfUpdateManager
from deployx.state import get_state_manager

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_deployx_env(tmp_path: Path, monkeypatch):
    """Isolates DeployX paths and global config for tests."""
    opt_dir = tmp_path / "opt_deployx"
    etc_dir = tmp_path / "etc_deployx"
    opt_dir.mkdir(parents=True, exist_ok=True)
    etc_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(paths, "root_dir", opt_dir)
    monkeypatch.setattr(paths, "projects_dir", opt_dir / "projects")
    monkeypatch.setattr(paths, "keys_dir", opt_dir / "keys")
    monkeypatch.setattr(paths, "backups_dir", opt_dir / "backups")
    monkeypatch.setattr(paths, "logs_dir", opt_dir / "logs")
    monkeypatch.setattr(paths, "state_dir", opt_dir / "state")
    monkeypatch.setattr(paths, "generated_dir", opt_dir / "generated")
    monkeypatch.setattr(paths, "config_dir", etc_dir)

    # Initialize directories
    for d in [paths.projects_dir, paths.keys_dir, paths.backups_dir, paths.logs_dir, paths.state_dir, paths.generated_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Seed global config
    global_cfg = etc_dir / "config.yml"
    global_cfg.write_text("version: '0.2.0'\nnetwork:\n  python:\n    selected_mirror: null\n", encoding="utf-8")

    # Mock git public repository verification to avoid live network calls during project registration
    monkeypatch.setattr(
        "deployx.deployment.project.verify_public_repository",
        lambda url, branch: "f0222384a29ebcd90d80111bfbc7c1abf8da4e0f",
    )


# ==============================================================================
# 1. URL Resolution & Zero-Touch Registration
# ==============================================================================
def test_extract_project_name_from_url():
    assert extract_project_name_from_url("git@github.com:acme-org/backend-api.git") == "backend-api"
    assert extract_project_name_from_url("https://github.com/acme/store_service.git") == "store_service"
    assert extract_project_name_from_url("https://gitlab.com/group/sub/my-web-app") == "my-web-app"
    assert is_git_url("git@github.com:foo/bar.git") is True
    assert is_git_url("https://github.com/foo/bar.git") is True
    assert is_git_url("my-project") is False


def test_zero_touch_deploy_git_url(mocker):
    console = Console(record=True)
    git_url = "https://github.com/acme/zero-touch-app.git"

    mocker.patch("deployx.git.repository.detect_remote_default_branch", return_value="main")
    mocker.patch("deployx.deployment.deploy.detect_remote_default_branch", return_value="main")
    mocker.patch("deployx.git.repository.GitRepositoryManager.sync_repository", return_value="c0ffee1234567890abcdef")
    mocker.patch("deployx.deployment.deploy.detect_repository", return_value=DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=DetectedInfrastructure(has_dockerfile=False),
    ))
    mock_cmd = MagicMock(success=True, returncode=0, stdout="", stderr="")
    mocker.patch("deployx.docker.compose.DockerComposeManager.build", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.up", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.run_transient", return_value=mock_cmd)
    mocker.patch("deployx.deployment.deploy.perform_http_healthcheck", return_value=True)

    success = run_deployment(git_url, yes=True, console=console)
    assert success is True
    assert project_exists("zero-touch-app") is True

    cfg = load_project_config("zero-touch-app")
    assert cfg.git.repository == git_url
    assert cfg.git.branch == "main"


# ==============================================================================
# 2. Port Conflict Auto-Resolution
# ==============================================================================
def test_port_conflict_auto_resolution(mocker):
    console = Console(record=True)
    add_project(name="portapp", git="https://github.com/org/app.git", branch="main", console=console)

    # Mock port 8000 as in use, 8001 as available
    def mock_is_port_in_use(port, host="0.0.0.0"):
        return port == 8000

    mocker.patch("deployx.deployment.deploy.is_port_in_use", side_effect=mock_is_port_in_use)
    mocker.patch("deployx.network.ports.is_port_in_use", side_effect=mock_is_port_in_use)
    mocker.patch("deployx.git.repository.GitRepositoryManager.sync_repository", return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f")
    mocker.patch("deployx.deployment.deploy.detect_repository", return_value=DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=DetectedInfrastructure(has_dockerfile=False),
    ))
    mock_cmd = MagicMock(success=True, returncode=0, stdout="", stderr="")
    mocker.patch("deployx.docker.compose.DockerComposeManager.build", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.up", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.run_transient", return_value=mock_cmd)
    mocker.patch("deployx.deployment.deploy.perform_http_healthcheck", return_value=True)

    success = run_deployment("portapp", yes=True, console=console)
    assert success is True

    # Port should have automatically changed from 8000 to 8001
    cfg = load_project_config("portapp")
    assert cfg.deployment.docker.port == 8001


# ==============================================================================
# 3. PyPI Mirror Selection & Build Failover
# ==============================================================================
def test_mirror_failover_manager(mocker):
    mocker.patch("deployx.network.mirrors.test_mirror", side_effect=[
        (False, 5.0, "Connection timed out"),  # first candidate fails
        (True, 0.4, "OK"),                     # second candidate succeeds
    ])
    mgr = MirrorFailoverManager(initial_mirror="https://failing.mirror/simple/")
    assert mgr.has_remaining_mirrors() is True
    fallback = mgr.get_next_fallback_mirror()
    assert fallback is not None
    assert "simple" in fallback


def test_build_retry_on_mirror_error(mocker):
    console = Console(record=True)
    add_project(name="mirrorfailapp", git="https://github.com/org/app.git", branch="main", console=console)

    mocker.patch("deployx.git.repository.GitRepositoryManager.sync_repository", return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f")
    mocker.patch("deployx.deployment.deploy.detect_repository", return_value=DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=DetectedInfrastructure(has_dockerfile=False),
    ))

    # First build call fails with mirror error, second succeeds
    fail_res = CommandError("pip could not find a version - connection timed out", command=["docker"], returncode=1)
    ok_cmd = MagicMock(success=True, returncode=0, stdout="", stderr="")

    build_mock = mocker.patch("deployx.docker.compose.DockerComposeManager.build", side_effect=[fail_res, ok_cmd])
    mocker.patch("deployx.docker.compose.DockerComposeManager.up", return_value=ok_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.run_transient", return_value=ok_cmd)
    mocker.patch("deployx.deployment.deploy.perform_http_healthcheck", return_value=True)
    mocker.patch("deployx.network.mirrors.test_mirror", return_value=(True, 0.2, "OK"))

    success = run_deployment("mirrorfailapp", yes=True, console=console)
    assert success is True
    assert build_mock.call_count == 2


# ==============================================================================
# 4. Dry Run & Explain Modes
# ==============================================================================
def test_dry_run_does_not_mutate_system(mocker):
    console = Console(record=True)
    add_project(name="dryapp", git="https://github.com/org/dry.git", branch="main", console=console)

    mocker.patch("deployx.git.repository.GitRepositoryManager.sync_repository", return_value="aabbcc123456")
    mocker.patch("deployx.deployment.deploy.detect_repository", return_value=DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=DetectedInfrastructure(has_dockerfile=False),
        explanation={"framework_reason": "Django manage.py found"},
    ))
    mock_build = mocker.patch("deployx.docker.compose.DockerComposeManager.build")
    mock_up = mocker.patch("deployx.docker.compose.DockerComposeManager.up")

    success = run_deployment("dryapp", dry_run=True, console=console)
    assert success is True
    assert mock_build.call_count == 0
    assert mock_up.call_count == 0


def test_cli_explain_command(mocker):
    console = Console(record=True)
    add_project(name="explapp", git="https://github.com/org/expl.git", branch="main", console=console)

    # Create dummy repo dir
    repo_dir = paths.get_project_repo_dir("explapp")
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "manage.py").write_text("# django\n", encoding="utf-8")

    res = runner.invoke(app, ["explain", "explapp"])
    assert res.exit_code == 0
    assert "Detection Explanation" in res.output
    assert "Framework" in res.output


# ==============================================================================
# 5. Auto-Rollback on Health Check Failure
# ==============================================================================
def test_auto_rollback_on_health_failure(mocker):
    console = Console(record=True)
    add_project(name="rollapp", git="https://github.com/org/app.git", branch="main", console=console)

    # Set previous commit in state
    state_mgr = get_state_manager()
    st = DeploymentState.new("rollapp", "https://github.com/org/app.git", "main")
    st.current_commit = "prevcommit123456"
    st.status = DeploymentStatus.HEALTHY
    st.deployed_at = "2026-09-30T10:00:00Z"
    state_mgr.save_state(st)

    mocker.patch("deployx.git.repository.GitRepositoryManager.sync_repository", return_value="newcommit789012")
    mocker.patch("deployx.deployment.deploy.detect_repository", return_value=DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=DetectedInfrastructure(has_dockerfile=False),
    ))
    mock_cmd = MagicMock(success=True, returncode=0, stdout="", stderr="")
    mocker.patch("deployx.docker.compose.DockerComposeManager.build", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.up", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.run_transient", return_value=mock_cmd)

    # Healthcheck fails for new commit
    mocker.patch("deployx.deployment.deploy.perform_http_healthcheck", return_value=False)
    rollback_mock = mocker.patch("deployx.deployment.rollback.rollback_project", return_value=True)

    success = run_deployment("rollapp", yes=True, auto_rollback=True, console=console)
    assert success is False
    assert rollback_mock.call_count == 1


# ==============================================================================
# 6. Timeline Events & Inspection
# ==============================================================================
def test_timeline_recording_and_cli(mocker):
    console = Console(record=True)
    add_project(name="timeapp", git="https://github.com/org/app.git", branch="main", console=console)

    st = DeploymentState.new("timeapp", "https://github.com/org/app.git", "main")
    st.record_event("preflight", "success", "Preflight checks passed")
    st.record_event("docker_build", "success", "Docker image built")
    st.record_event("healthcheck", "success", "Application healthy")
    get_state_manager().save_state(st)

    timeline = get_project_timeline("timeapp")
    assert len(timeline) == 3
    assert timeline[0]["stage"] == "preflight"
    assert timeline[2]["status"] == "success"

    # CLI events
    res = runner.invoke(app, ["events", "timeapp"])
    assert res.exit_code == 0
    assert "Deployment Timeline" in res.output

    # CLI events --json
    res_json = runner.invoke(app, ["events", "timeapp", "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert len(data) == 3

    # CLI inspect --json
    res_insp = runner.invoke(app, ["inspect", "timeapp", "--json"])
    assert res_insp.exit_code == 0
    insp_data = json.loads(res_insp.output)
    assert insp_data["project"] == "timeapp"
    assert "state" in insp_data


# ==============================================================================
# 7. Global & Project Config CLI
# ==============================================================================
def test_global_config_cli():
    # show
    res = runner.invoke(app, ["config", "show"])
    assert res.exit_code == 0

    # show --json
    res_json = runner.invoke(app, ["config", "show", "--json"])
    assert res_json.exit_code == 0
    cfg = json.loads(res_json.output)
    assert "version" in cfg

    # set & unset
    res_set = runner.invoke(app, ["config", "set", "network.proxy.enabled", "true"])
    assert res_set.exit_code == 0
    assert load_global_config()["network"]["proxy"]["enabled"] is True

    res_unset = runner.invoke(app, ["config", "unset", "network.proxy.enabled"])
    assert res_unset.exit_code == 0
    assert "enabled" not in load_global_config().get("network", {}).get("proxy", {})

    # reset
    res_reset = runner.invoke(app, ["config", "reset", "--force"])
    assert res_reset.exit_code == 0


def test_project_config_cli():
    add_project(name="projcfgapp", git="https://github.com/org/app.git", branch="main")

    # show
    res = runner.invoke(app, ["project", "config", "show", "projcfgapp"])
    assert res.exit_code == 0

    # show --json
    res_json = runner.invoke(app, ["project", "config", "show", "projcfgapp", "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert data["project"]["name"] == "projcfgapp"

    # set build timeout
    res_set = runner.invoke(app, ["project", "config", "set", "projcfgapp", "deployment.build_timeout", "1800"])
    assert res_set.exit_code == 0
    cfg = load_project_config("projcfgapp")
    assert cfg.deployment.build_timeout == 1800


# ==============================================================================
# 8. Domain & Reverse Proxy Management
# ==============================================================================
def test_domain_cli_and_proxy_manager(mocker):
    add_project(name="domainapp", git="https://github.com/org/app.git", branch="main")

    mock_proxy = mocker.patch("deployx.network.proxy.ProxyManager.add_or_update_route", return_value=True)
    res = runner.invoke(app, ["domain", "add", "domainapp", "api.example.com"])
    assert res.exit_code == 0
    assert mock_proxy.call_count == 1

    cfg = load_project_config("domainapp")
    assert cfg.deployment.domain == "api.example.com"

    mock_remove = mocker.patch("deployx.network.proxy.ProxyManager.remove_route", return_value=True)
    res_rem = runner.invoke(app, ["domain", "remove", "domainapp"])
    assert res_rem.exit_code == 0
    assert mock_remove.call_count == 1

    cfg_after = load_project_config("domainapp")
    assert cfg_after.deployment.domain is None


# ==============================================================================
# 9. Init Wizard & System Cleanup
# ==============================================================================
def test_init_wizard(mocker):
    console = Console(record=True)
    mocker.patch("deployx.init_wizard.auto_select_best_mirror", return_value=("https://pypi.org/simple/", {}))
    success = run_init_wizard(non_interactive=True, console=console)
    assert success is True
    assert (paths.config_dir / "config.yml").is_file()


def test_system_cleanup(mocker):
    console = Console(record=True)
    mock_run = mocker.patch("deployx.cleanup.run_command", return_value=CommandResult(command=[], returncode=0, stdout="", stderr="", duration=0.1))
    summary = run_system_cleanup(orphans=True, delete_volumes=True, force=True, console=console)
    assert "pruned_containers" in summary
    assert mock_run.call_count >= 3


# ==============================================================================
# 10. Self Update & Status JSON
# ==============================================================================
def test_self_update_cli(mocker):
    mocker.patch("deployx.self_update.SelfUpdateManager.check_for_update", return_value=(False, "0.2.0"))
    res = runner.invoke(app, ["self-update", "--check"])
    assert res.exit_code == 0
    assert "DeployX is up to date" in res.output


def test_status_json_cli(mocker):
    add_project(name="statjsonapp", git="https://github.com/org/app.git", branch="main")
    res = runner.invoke(app, ["status", "statjsonapp", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["project"] == "statjsonapp"
    assert "deployment_status" in data
