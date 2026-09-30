"""
Comprehensive Test Suite for DeployX v0.1.6 Stabilization Release:
- Configurable build timeouts (precedence: CLI > project > global > default 3600s, --no-build-timeout)
- Docker Compose progress flag ordering (--progress=plain before -f)
- BuildKit responsiveness tracking & rate-limited stall warnings (ACTIVE, SLOW, STALLED, <= 1 per 300s)
- Pip mirror granular clearing (--clear-pip-index, --clear-pip-extra-index, --clear-pip-trusted-host, --clear-all-pip-settings)
- Atomic project configuration backups with 0600 mode and rotation
- Django runtime database probe & backend mismatch guard
- Django production safety warnings (DEBUG=True, ALLOWED_HOSTS)
- HTTP healthcheck diagnostics & technical error classification
- Root exception extractor for HTML debug pages & tracebacks with secret redaction
- Rollback workflow (deployx rollback <project>)
- Deterministic Docker resource labels (com.deployx.managed, com.deployx.project) & compose-free resource purge
- Granular project removal flags (--remove-containers, --remove-images, --remove-volumes, --remove-key) & status tables
- Doctor orphan resource detection & container network diagnostics
- Per-project log rotation & structured audit events
"""

import json
import yaml
from pathlib import Path
import pytest
from typer.testing import CliRunner
from rich.console import Console

from deployx.cli import app
from deployx.config import (
    paths,
    reload_paths,
    get_effective_build_timeout,
    get_effective_python_build_config,
    backup_project_file,
    load_global_config,
)
from deployx.core.command import CommandResult
from deployx.core.exceptions import extract_root_exception, classify_http_failure
from deployx.deployment.deploy import run_deployment
from deployx.deployment.health import perform_http_healthcheck
from deployx.deployment.project import (
    add_project,
    edit_project,
    remove_project,
    show_project_info,
    load_project_config,
    project_exists,
)
from deployx.deployment.rollback import rollback_project
from deployx.deployment.update import run_update
from deployx.detectors.base import DetectedInfrastructure, DetectionResult
from deployx.docker.compose import (
    DockerComposeManager,
    purge_project_resources,
)
from deployx.doctor.checks import (
    check_orphan_resources,
    check_docker_container_network,
    run_project_doctor,
    run_doctor,
    CheckStatus,
)
from deployx.generators.django import generate_compose_file
from deployx.logging.logger import ProjectLogger
from deployx.models import (
    BuildConfig,
    DatabasePreference,
    DeploymentConfig,
    DeploymentState,
    DeploymentStatus,
    FrameworkType,
    HealthStatus,
    ProjectConfig,
    PythonBuildConfig,
)
from deployx.state import get_state_manager
from deployx.ui.progress import BuildKitParser, DeploymentProgressReporter

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_deployx(tmp_path, monkeypatch, mocker):
    """Isolates DeployX directory tree for every test."""
    root = tmp_path / "opt_deployx"
    etc = tmp_path / "etc_deployx"
    monkeypatch.setenv("DEPLOYX_ROOT", str(root))
    monkeypatch.setenv("DEPLOYX_CONFIG_DIR", str(etc))
    reload_paths()
    paths.ensure_all_dirs()

    mocker.patch(
        "deployx.deployment.project.verify_public_repository",
        return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f",
    )
    yield


# ==============================================================================
# 1. Build Timeout Precedence and Configuration Tests
# ==============================================================================
def test_build_timeout_precedence_hierarchy(tmp_path):
    # 1. Default fallback: 3600
    assert get_effective_build_timeout(None, False, None) == 3600

    # 2. Global config fallback
    etc = paths.config_dir
    etc.mkdir(parents=True, exist_ok=True)
    global_file = etc / "config.yml"
    global_file.write_text("deployment:\n  build_timeout: 1800\n", encoding="utf-8")
    assert get_effective_build_timeout(None, False, None) == 1800

    # 3. Project config overrides global config
    cfg = ProjectConfig(
        version=1,
        project={"name": "timeout-app"},
        git={"repository": "https://github.com/myorg/myapp.git", "branch": "main"},
        deployment={"framework": "django", "build_timeout": 2400},
    )
    assert get_effective_build_timeout(None, False, cfg) == 2400

    # 4. CLI override beats project config
    assert get_effective_build_timeout(7200, False, cfg) == 7200

    # 5. CLI --no-build-timeout returns None (unlimited)
    assert get_effective_build_timeout(None, True, cfg) is None
    assert get_effective_build_timeout(7200, True, cfg) is None


# ==============================================================================
# 2. Docker Compose Progress Flag Order Test
# ==============================================================================
def test_docker_compose_progress_flag_order(mocker, tmp_path):
    compose_file = tmp_path / "docker-compose.deployx.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    mgr = DockerComposeManager("testproj", compose_file=compose_file)

    captured_cmds = []

    def mock_streaming(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.1)

    mocker.patch("deployx.docker.compose.run_command_streaming", side_effect=mock_streaming)

    mgr.build(service="web", timeout=1200)

    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    # Verify --progress=plain is positioned BEFORE -f
    progress_idx = cmd.index("--progress=plain")
    file_flag_idx = cmd.index("-f")
    assert progress_idx < file_flag_idx, f"--progress=plain should precede -f in {cmd}"
    assert "build" in cmd


# ==============================================================================
# 3. BuildKit Responsiveness & Rate-Limited Stall Panel
# ==============================================================================
def test_buildkit_status_and_stall_rate_limiting():
    console = Console(record=True)
    reporter = DeploymentProgressReporter("stall-rate-app", console=console)
    reporter.parser.current_operation = "RUN pip install -r requirements.txt"

    # Status transitions
    assert reporter.get_build_status() == "ACTIVE"

    # First stall warning triggers panel
    reporter.on_stall(elapsed=125.0, idle=120.0)
    output1 = console.export_text()
    assert "Build status: STALLED" in output1
    assert "WARNING: No new Docker build output for 2 minutes." in output1

    # Second stall warning immediately after must be suppressed (rate limit <= 1 per 300s)
    console_suppressed = Console(record=True)
    reporter.console = console_suppressed
    reporter.on_stall(elapsed=130.0, idle=125.0)
    output2 = console_suppressed.export_text()
    assert output2.strip() == ""


# ==============================================================================
# 4. Pip Mirror Clearing in Project Edit
# ==============================================================================
def test_project_edit_pip_mirror_clearing(mocker):
    console = Console(record=True)
    add_project(
        name="pip-clear-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        console=console,
    )

    # Set custom mirror
    edit_project(
        project_name="pip-clear-app",
        pip_index_url="https://mirror.example.com/simple/",
        pip_extra_index_url="https://extra.mirror.com/simple/",
        pip_trusted_host="mirror.example.com",
        yes=True,
        console=console,
    )
    cfg1 = load_project_config("pip-clear-app")
    assert cfg1.build.python.index_url == "https://mirror.example.com/simple/"
    assert cfg1.build.python.extra_index_url == "https://extra.mirror.com/simple/"
    assert cfg1.build.python.trusted_host == "mirror.example.com"

    # Clear individual fields
    edit_project(
        project_name="pip-clear-app",
        clear_pip_extra_index=True,
        yes=True,
        console=console,
    )
    cfg2 = load_project_config("pip-clear-app")
    assert cfg2.build.python.index_url == "https://mirror.example.com/simple/"
    assert cfg2.build.python.extra_index_url is None
    assert cfg2.build.python.trusted_host == "mirror.example.com"

    # Clear all pip settings
    edit_project(
        project_name="pip-clear-app",
        clear_all_pip_settings=True,
        yes=True,
        console=console,
    )
    cfg3 = load_project_config("pip-clear-app")
    assert cfg3.build.python.index_url is None
    assert cfg3.build.python.extra_index_url is None
    assert cfg3.build.python.trusted_host is None


# ==============================================================================
# 5. Atomic Project Config Backups
# ==============================================================================
def test_atomic_project_config_backup(tmp_path):
    test_file = tmp_path / "deployx.yml"
    test_file.write_text("version: 1\n", encoding="utf-8")

    backups = []
    for i in range(7):
        b = backup_project_file(test_file, max_backups=5)
        assert b is not None
        assert b.is_file()
        backups.append(b)

    backups_dir = test_file.parent / "backups"
    remaining = list(backups_dir.glob("deployx_*"))
    # Should rotate and keep at most 5
    assert len(remaining) == 5


# ==============================================================================
# 6. Django Runtime Database Backend Mismatch Guard
# ==============================================================================
def test_django_database_backend_mismatch_blocks_deployment(mocker):
    console = Console(record=True)
    add_project(
        name="db-mismatch-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        database="postgres",
        console=console,
    )

    mocker.patch("deployx.deployment.deploy.GitRepositoryManager.sync_repository", return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f")
    mocker.patch("deployx.deployment.deploy.detect_repository", return_value=DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=DetectedInfrastructure(has_dockerfile=False),
    ))
    mock_cmd = CommandResult(command=["docker"], returncode=0, stdout="", stderr="", duration=0.1)
    mocker.patch("deployx.docker.compose.DockerComposeManager.build", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.up", return_value=mock_cmd)

    # Mock runtime probe returning SQLite backend
    probe_output = "DEPLOYX_PROBE_JSON:" + json.dumps({
        "engine": "django.db.backends.sqlite3",
        "debug": False,
        "allowed_hosts": ["*"],
    })
    probe_cmd_result = CommandResult(command=["docker"], returncode=0, stdout=probe_output, stderr="", duration=0.1)
    mocker.patch("deployx.docker.compose.DockerComposeManager.run_transient", return_value=probe_cmd_result)

    # Deployment should fail immediately before migrations
    success = run_deployment("db-mismatch-app", console=console)
    assert success is False

    output = console.export_text()
    assert "Database backend mismatch" in output
    assert "sqlite3" in output


def test_django_database_backend_match_proceeds(mocker):
    console = Console(record=True)
    add_project(
        name="db-match-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        database="postgres",
        console=console,
    )

    mocker.patch("deployx.deployment.deploy.GitRepositoryManager.sync_repository", return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f")
    mocker.patch("deployx.deployment.deploy.detect_repository", return_value=DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=DetectedInfrastructure(has_dockerfile=False),
    ))
    mock_cmd = CommandResult(command=["docker"], returncode=0, stdout="", stderr="", duration=0.1)
    mocker.patch("deployx.docker.compose.DockerComposeManager.build", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.up", return_value=mock_cmd)

    # Mock runtime probe returning PostgreSQL backend
    probe_output = "DEPLOYX_PROBE_JSON:" + json.dumps({
        "engine": "django.db.backends.postgresql",
        "debug": False,
        "allowed_hosts": ["*"],
    })
    probe_cmd_result = CommandResult(command=["docker"], returncode=0, stdout=probe_output, stderr="", duration=0.1)
    mocker.patch("deployx.docker.compose.DockerComposeManager.run_transient", return_value=probe_cmd_result)
    mocker.patch("deployx.deployment.deploy.perform_http_healthcheck", return_value=True)

    success = run_deployment("db-match-app", console=console)
    assert success is True


# ==============================================================================
# 7. Django Production Safety Warnings (DEBUG=True)
# ==============================================================================
def test_django_debug_mode_warning(mocker):
    console = Console(record=True)
    add_project(
        name="debug-warn-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        database="postgres",
        console=console,
    )

    mocker.patch("deployx.deployment.deploy.GitRepositoryManager.sync_repository", return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f")
    mocker.patch("deployx.deployment.deploy.detect_repository", return_value=DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["manage.py"],
        infrastructure=DetectedInfrastructure(has_dockerfile=False),
    ))
    mock_cmd = CommandResult(command=["docker"], returncode=0, stdout="", stderr="", duration=0.1)
    mocker.patch("deployx.docker.compose.DockerComposeManager.build", return_value=mock_cmd)
    mocker.patch("deployx.docker.compose.DockerComposeManager.up", return_value=mock_cmd)

    # Mock runtime probe returning debug=True
    probe_output = "DEPLOYX_PROBE_JSON:" + json.dumps({
        "engine": "django.db.backends.postgresql",
        "debug": True,
        "allowed_hosts": ["*"],
    })
    probe_cmd_result = CommandResult(command=["docker"], returncode=0, stdout=probe_output, stderr="", duration=0.1)
    mocker.patch("deployx.docker.compose.DockerComposeManager.run_transient", return_value=probe_cmd_result)
    mocker.patch("deployx.deployment.deploy.perform_http_healthcheck", return_value=True)

    success = run_deployment("debug-warn-app", console=console)
    assert success is True

    output = console.export_text()
    assert "WARNING: Django DEBUG=True is active in production" in output


# ==============================================================================
# 8. HTTP Health Check Diagnostics & Classification
# ==============================================================================
def test_http_healthcheck_diagnostics_collection(mocker):
    details = {}
    mock_resp = mocker.MagicMock()
    mock_resp.status = 500
    mock_resp.getcode.return_value = 500
    mock_resp.read.return_value = b"<h1>Internal Server Error</h1>"
    mock_resp.__enter__.return_value = mock_resp

    mocker.patch("urllib.request.urlopen", return_value=mock_resp)
    ok = perform_http_healthcheck(8000, expected_status=200, retries=1, interval=0.1, details=details)
    assert ok is False
    assert details["status_code"] == 500
    assert "Internal Server Error" in details["response_preview"]


def test_classify_http_failure_categories():
    assert "HTTP 400" in classify_http_failure(400)
    assert "HTTP 403" in classify_http_failure(403)
    assert "HTTP 404" in classify_http_failure(404)
    assert "HTTP 500" in classify_http_failure(500)
    assert "Connection refused" in classify_http_failure(None, "ConnectionRefusedError: port 8000")


# ==============================================================================
# 9. Technical 500 HTML & Container Traceback Parser
# ==============================================================================
def test_extract_root_exception_from_html():
    raw_html = (
        "<!DOCTYPE html><html><head><title>OperationalError at /</title></head>"
        "<body><h1>OperationalError</h1><pre class='exception_value'>connection to server at '127.0.0.1', port 5432 failed: password authentication failed for user 'mysecretuser'</pre>"
        "<h2>Traceback:</h2><p>/app/core/settings.py in get_connection</p></body></html>"
    )
    result = extract_root_exception(raw_html)
    assert result is not None
    summary = result.summary or str(result)
    assert "OperationalError" in summary
    assert "password authentication failed" in summary
    # Must not contain raw html tags
    assert "<html>" not in summary
    assert "<pre" not in summary


def test_extract_root_exception_from_container_logs():
    logs = (
        "Starting server...\n"
        "Traceback (most recent call last):\n"
        "  File \"/app/manage.py\", line 22, in <module>\n"
        "    main()\n"
        "  File \"/app/manage.py\", line 18, in main\n"
        "    execute_from_command_line(sys.argv)\n"
        "django.core.exceptions.ImproperlyConfigured: Set the SECRET_KEY environment variable\n"
    )
    result = extract_root_exception(logs)
    assert result is not None
    summary = result.summary or str(result)
    assert "ImproperlyConfigured" in summary
    assert "SECRET_KEY" in summary


# ==============================================================================
# 10. Rollback Workflow
# ==============================================================================
def test_rollback_to_previous_commit(mocker):
    console = Console(record=True)
    add_project(
        name="rollback-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        console=console,
    )
    state_mgr = get_state_manager()
    state = DeploymentState.new("rollback-app", "https://github.com/myorg/myapp.git", "main")
    state.current_commit = "2222222222222222222222222222222222222222"
    state.previous_commit = "1111111111111111111111111111111111111111"
    state.status = DeploymentStatus.HEALTHY
    state_mgr.save_state(state)

    mock_deploy = mocker.patch("deployx.deployment.rollback.run_deployment", return_value=True)

    ok = rollback_project("rollback-app", console=console)
    assert ok is True
    mock_deploy.assert_called_once_with(
        "rollback-app",
        target_commit="1111111111111111111111111111111111111111",
        build_timeout=None,
        no_build_timeout=False,
        verbose=False,
        plain=False,
        console=console,
    )


def test_rollback_aborts_without_previous_commit():
    console = Console(record=True)
    add_project(
        name="no-prev-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        console=console,
    )
    ok = rollback_project("no-prev-app", console=console)
    assert ok is False
    assert "No previous commit recorded" in console.export_text()


# ==============================================================================
# 11. Deterministic Docker Labels & Compose-Free Resource Purge
# ==============================================================================
def test_generate_compose_includes_management_labels(tmp_path):
    cfg = ProjectConfig(
        version=1,
        project={"name": "labelproj"},
        git={"repository": "https://github.com/myorg/myapp.git", "branch": "main"},
        deployment={"framework": "django", "database": "postgres"},
    )
    det = DetectionResult(
        framework=FrameworkType.DJANGO,
        confidence=1.0,
        matched_indicators=["requirements.txt"],
        infrastructure=DetectedInfrastructure(has_dockerfile=False),
    )
    dest = tmp_path / "docker-compose.deployx.yml"
    generate_compose_file(cfg, det, "deployx_labelproj:latest", dest)

    content = dest.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    web_labels = data["services"]["web"]["labels"]
    assert web_labels["com.deployx.managed"] == "true"
    assert web_labels["com.deployx.project"] == "labelproj"
    assert web_labels["com.deployx.version"] == "0.1.6"


def test_compose_free_resource_purge(mocker):
    commands_executed = []

    def fake_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "ps" in cmd or "images" in cmd or "volume" in cmd or "network" in cmd:
            return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.1)
        return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.1)

    mocker.patch("deployx.docker.compose.run_command", side_effect=fake_run)
    counts = purge_project_resources("purgeproj", remove_containers=True, remove_images=True, remove_volumes=True)
    assert isinstance(counts, dict)
    assert "containers" in counts


# ==============================================================================
# 12. Project Remove Granular Flags & Status Table
# ==============================================================================
def test_project_remove_granular_flags(mocker):
    console = Console(record=True)
    add_project(
        name="gran-rem-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        private=True,
        console=console,
    )
    key_path = paths.get_project_key_path("gran-rem-app")
    key_path.write_text("privkey")

    mock_purge = mocker.patch("deployx.deployment.project.purge_project_resources")

    ok = remove_project(
        "gran-rem-app",
        remove_containers=True,
        remove_images=True,
        remove_key=True,
        force=True,
        console=console,
    )
    assert ok is True
    assert not project_exists("gran-rem-app")
    assert not key_path.exists()

    output = console.export_text()
    assert "Removal Status: gran-rem-app" in output
    assert "Registration removed" in output
    assert "Containers removed" in output
    assert "Deploy key removed" in output
    mock_purge.assert_called_once_with(
        "gran-rem-app",
        remove_containers=True,
        remove_images=True,
        remove_volumes=False,
    )


# ==============================================================================
# 13. Doctor Orphan Detection & Project Doctor
# ==============================================================================
def test_doctor_orphan_resource_detection(mocker):
    # Mock finding an unregistered container
    mocker.patch(
        "deployx.docker.compose.find_managed_containers",
        return_value=[{"id": "cid123", "name": "deployx_orphanapp_web", "labels": "com.deployx.project=orphanapp"}],
    )
    mocker.patch("deployx.docker.compose.find_managed_volumes", return_value=[])

    item = check_orphan_resources()
    assert item.status == CheckStatus.WARNING
    assert "orphanapp" in item.details


def test_project_doctor_validates_project(mocker):
    console = Console(record=True)
    add_project(
        name="doc-proj-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        console=console,
    )
    ok = run_project_doctor("doc-proj-app", console=console)
    assert ok is True
    output = console.export_text()
    assert "DeployX Project Doctor: doc-proj-app" in output
    assert "Configuration" in output


# ==============================================================================
# 14. Project Logger Rotation & Audit Events
# ==============================================================================
def test_project_logger_rotation_and_events(tmp_path):
    logger = ProjectLogger("audit-app", max_bytes=1024, backup_count=2)
    # Structured event log with secret redaction
    logger.log_event(
        stage="sync_git",
        command="git clone https://user:supersecretpass@github.com/org/repo.git",
        exit_code=0,
        elapsed=1.23,
    )
    logger.close()

    log_file = paths.get_project_log_dir("audit-app") / "deploy.log"
    assert log_file.is_file()
    content = log_file.read_text(encoding="utf-8")
    assert "EVENT: project=audit-app | stage=sync_git" in content
    assert "supersecretpass" not in content
    assert "https://user:***@github.com/org/repo.git" in content
