"""
Comprehensive Test Suite for DeployX v0.1.4 Progress Reporting & Streaming:
- Real-time command streaming (stdout and stderr)
- Exit code propagation
- Live secret redaction during streaming
- Ctrl+C (KeyboardInterrupt) process termination
- Silent process activity heartbeat emission
- Long-running stall warning and contextual network hints
- BuildKit step counter and percentage parsing
- Unknown total step count handling ('active' fallback)
- Verbose mode (--verbose)
- Plain / CI mode (--plain / --no-progress)
- Deployment stage percentage derivation
- Structured failure summary on build error
- Update pipeline streaming behavior
"""

import sys
import time
from pathlib import Path
import pytest
from rich.console import Console
from typer.testing import CliRunner

from deployx.cli import app
from deployx.config import paths, reload_paths
from deployx.core.command import CommandError, run_command_streaming
from deployx.deployment.deploy import run_deployment
from deployx.deployment.project import add_project
from deployx.deployment.update import run_update
from deployx.detectors.base import DetectedInfrastructure, DetectionResult, FrameworkType
from deployx.models import DeploymentState, DeploymentStatus, HealthStatus
from deployx.state import get_state_manager
from deployx.ui.progress import BuildKitParser, DeploymentProgressReporter, format_duration, make_ascii_progress_bar


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
# 1. Real-time Command Streaming (stdout & stderr)
# ==============================================================================
def test_streaming_command_output():
    streamed_lines = []
    cmd = [sys.executable, "-c", "import sys; print('line1'); print('line2')"]
    res = run_command_streaming(cmd, on_line=lambda l: streamed_lines.append(l))
    assert res.returncode == 0
    assert "line1" in streamed_lines
    assert "line2" in streamed_lines
    assert "line1" in res.stdout
    assert "line2" in res.stdout


def test_streaming_stderr():
    stderr_lines = []
    cmd = [sys.executable, "-c", "import sys; sys.stderr.write('err_msg\\n')"]
    res = run_command_streaming(cmd, on_stderr_line=lambda l: stderr_lines.append(l))
    assert res.returncode == 0
    assert "err_msg" in stderr_lines
    assert "err_msg" in res.stderr


# ==============================================================================
# 2. Exit Code Propagation
# ==============================================================================
def test_streaming_exit_code_propagation():
    cmd = [sys.executable, "-c", "import sys; print('failing'); sys.exit(42)"]
    with pytest.raises(CommandError) as exc_info:
        run_command_streaming(cmd, check=True)
    assert exc_info.value.returncode == 42
    assert "failing" in exc_info.value.stdout

    # With check=False, returns CommandResult with returncode 42
    res = run_command_streaming(cmd, check=False)
    assert res.returncode == 42
    assert res.success is False


# ==============================================================================
# 3. Secret Redaction During Streaming
# ==============================================================================
def test_streaming_secret_redaction():
    secret_token = "ghp_super_secret_github_token_xyz"
    streamed_lines = []
    cmd = [sys.executable, "-c", f"print('Token: {secret_token}')"]
    res = run_command_streaming(
        cmd,
        sensitive_strings=[secret_token],
        on_line=lambda l: streamed_lines.append(l),
    )
    assert secret_token not in res.stdout
    assert "[REDACTED]" in res.stdout
    assert any("[REDACTED]" in l for l in streamed_lines)
    assert not any(secret_token in l for l in streamed_lines)


# ==============================================================================
# 4. Ctrl+C (KeyboardInterrupt) Process Termination
# ==============================================================================
def test_streaming_ctrl_c_cleanup(mocker):
    mock_proc = mocker.MagicMock()
    mock_proc.stdout.readline.return_value = ""
    mock_proc.stderr.readline.return_value = ""
    mock_proc.poll.return_value = None
    mock_proc.wait.return_value = -2

    mocker.patch("subprocess.Popen", return_value=mock_proc)
    mocker.patch("queue.Queue.get", side_effect=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_command_streaming(["dummy", "command"])

    assert mock_proc.terminate.called or mock_proc.kill.called


def test_deployment_ctrl_c_handled_cleanly(mocker):
    console = Console(record=True)
    add_project(
        name="interrupted-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        console=console,
    )
    # Trigger KeyboardInterrupt during git sync
    mocker.patch(
        "deployx.deployment.deploy.GitRepositoryManager.sync_repository",
        side_effect=KeyboardInterrupt(),
    )

    success = run_deployment("interrupted-app", console=console)
    assert success is False
    output = console.export_text()
    assert "Deployment cancelled by user." in output

    state = get_state_manager().get_state("interrupted-app")
    assert state is not None
    assert state.status == DeploymentStatus.FAILED
    assert "cancelled by user" in state.last_error.lower()


# ==============================================================================
# 5. Heartbeat Emission
# ==============================================================================
def test_streaming_heartbeat_emission(mocker):
    heartbeat_calls = []
    # Run a script that sleeps briefly to trigger low-interval heartbeat
    cmd = [
        sys.executable,
        "-c",
        "import time; time.sleep(0.35); print('done')",
    ]
    res = run_command_streaming(
        cmd,
        heartbeat_interval=0.1,  # short interval for test
        on_heartbeat=lambda elapsed, idle: heartbeat_calls.append((elapsed, idle)),
    )
    assert res.returncode == 0
    assert len(heartbeat_calls) >= 1
    assert heartbeat_calls[0][0] >= 0.1  # elapsed >= 0.1


# ==============================================================================
# 6. Stall Warning & Contextual Hints
# ==============================================================================
def test_streaming_stall_warning(mocker):
    stall_calls = []
    cmd = [
        sys.executable,
        "-c",
        "import time; time.sleep(0.35); print('done')",
    ]
    res = run_command_streaming(
        cmd,
        stall_timeout=0.15,
        on_stall=lambda elapsed, idle: stall_calls.append((elapsed, idle)),
    )
    assert res.returncode == 0
    assert len(stall_calls) >= 1


def test_progress_reporter_stall_warning_output():
    console = Console(record=True)
    reporter = DeploymentProgressReporter("stall-app", console=console)
    reporter.parser.current_operation = "RUN pip install -r requirements.txt"
    reporter.on_stall(elapsed=125.0, idle=120.0)

    output = console.export_text()
    assert "WARNING: No new Docker build output for 2 minutes." in output
    assert "RUN pip install" in output
    assert "requirements.txt" in output
    assert "Network or package index connectivity may be slow" in output
    assert "deployx deploy stall-app --verbose" in output


# ==============================================================================
# 7. BuildKit Step Parsing
# ==============================================================================
def test_buildkit_step_parsing():
    parser = BuildKitParser()
    parser.parse_line("#7 [4/7] RUN pip install -r requirements.txt")
    assert parser.step_current == 4
    assert parser.step_total == 7
    assert parser.approx_percent == 57  # int((4/7)*100)
    assert "pip install" in parser.current_operation
    assert parser.get_progress_label() == "4/7 steps (~57%)"
    assert parser.is_network_operation() is True

    # Legacy Step syntax
    parser.parse_line("Step 8/10 : WORKDIR /app")
    assert parser.step_current == 8
    assert parser.step_total == 10
    assert parser.approx_percent == 80
    assert parser.get_progress_label() == "8/10 steps (~80%)"


# ==============================================================================
# 8. Unknown Total Step Handling ('active' fallback)
# ==============================================================================
def test_unknown_total_step_handling():
    parser = BuildKitParser()
    parser.parse_line("#1 [internal] load build definition from Dockerfile")
    assert parser.step_current is None
    assert parser.step_total is None
    assert parser.approx_percent is None
    assert parser.get_progress_label() == "active"

    parser.parse_line("#2 [internal] load metadata for docker.io/library/python:3.12-slim")
    assert parser.get_progress_label() == "active"
    assert parser.is_network_operation() is True


# ==============================================================================
# 9. Verbose Mode
# ==============================================================================
def test_verbose_mode_streams_output():
    console = Console(record=True)
    reporter = DeploymentProgressReporter("verbose-app", verbose=True, console=console)
    reporter.on_build_output("#5 [2/5] COPY . .")
    output = console.export_text()
    assert "#5 [2/5] COPY . ." in output


def test_cli_deploy_verbose_flag(mocker):
    mocker.patch("deployx.deployment.deploy.run_deployment", return_value=True)
    res = runner.invoke(app, ["deploy", "testproj", "--verbose"])
    assert res.exit_code == 0


def test_cli_update_verbose_flag(mocker):
    mocker.patch("deployx.deployment.update.run_update", return_value=True)
    res = runner.invoke(app, ["update", "testproj", "--verbose"])
    assert res.exit_code == 0


# ==============================================================================
# 10. Plain / CI Mode
# ==============================================================================
def test_plain_mode_clean_text():
    console = Console(record=True)
    reporter = DeploymentProgressReporter("plain-app", plain=True, console=console)
    reporter.start_stage(7, "Building Docker image")
    reporter.on_build_output("#5 [3/8] WORKDIR /app")
    output = console.export_text()
    assert "[7/12] Building Docker image..." in output
    assert "Overall: 58%" in output
    assert "[Build ~37%] Step 3/8: WORKDIR /app" in output


def test_cli_deploy_plain_flag(mocker):
    mocker.patch("deployx.deployment.deploy.run_deployment", return_value=True)
    res = runner.invoke(app, ["deploy", "testproj", "--plain"])
    assert res.exit_code == 0

    res2 = runner.invoke(app, ["deploy", "testproj", "--no-progress"])
    assert res2.exit_code == 0


# ==============================================================================
# 11. Deployment Stage Percentage Derivation
# ==============================================================================
def test_deployment_stage_percentage():
    console = Console(record=True)
    reporter = DeploymentProgressReporter("pct-app", total_stages=12, console=console)

    reporter.current_stage = 1
    assert reporter.overall_percentage == 8  # 1/12

    reporter.current_stage = 7
    assert reporter.overall_percentage == 58  # 7/12

    reporter.current_stage = 12
    assert reporter.overall_percentage == 100

    bar = make_ascii_progress_bar(58, width=20)
    assert len(bar) == 22  # '[' + 20 chars + ']'
    assert "█" in bar
    assert "░" in bar

    assert format_duration(75) == "01:15"
    assert format_duration(3665) == "01:01:05"


# ==============================================================================
# 12. Failure Summary Panel
# ==============================================================================
def test_failure_summary_panel():
    console = Console(record=True)
    reporter = DeploymentProgressReporter("fail-app", console=console)
    reporter.start_stage(7, "Building Docker image")
    reporter.parser.current_operation = "RUN pip install -r requirements.txt"
    reporter.parser.last_relevant_lines = [
        "Step 4/7 : RUN pip install -r requirements.txt",
        "ERROR: Could not find a version that satisfies the requirement invalid-pkg",
    ]

    reporter.show_failure_summary(exit_code=1, exception_msg="Build failed")
    output = console.export_text()
    assert "Deployment failed at:" in output
    assert "Step 7/12 - Building Docker image" in output
    assert "RUN pip install -r requirements.txt" in output
    assert "Exit code:" in output
    assert "1" in output
    assert "ERROR: Could not find a version" in output
    assert "deployx logs fail-app" in output


# ==============================================================================
# 13. Update Streaming Behavior
# ==============================================================================
def test_update_streaming_forwards_flags(mocker):
    console = Console(record=True)
    add_project(
        name="update-stream-app",
        git="https://github.com/myorg/myapp.git",
        branch="main",
        console=console,
    )
    # Set state
    state_mgr = get_state_manager()
    old_state = DeploymentState.new("update-stream-app", "https://github.com/myorg/myapp.git", "main")
    old_state.current_commit = "f0222384a29ebcd90d80111bfbc7c1abf8da4e0f"
    state_mgr.save_state(old_state)

    mocker.patch(
        "deployx.git.repository.GitRepositoryManager.get_remote_commit",
        return_value="4ba128c704f056d61f1cf01bfbc7c1abf8da4e0f",
    )
    mock_deploy = mocker.patch("deployx.deployment.update.run_deployment", return_value=True)

    res = run_update("update-stream-app", verbose=True, plain=True, console=console)
    assert res is True
    mock_deploy.assert_called_once_with(
        "update-stream-app",
        target_commit="4ba128c704f056d61f1cf01bfbc7c1abf8da4e0f",
        verbose=True,
        plain=True,
        console=console,
    )
