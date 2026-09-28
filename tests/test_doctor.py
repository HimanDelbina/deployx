"""
Tests for DeployX Doctor diagnostics.
"""

from unittest.mock import MagicMock, patch
from rich.console import Console

from deployx.doctor.checks import (
    CheckStatus,
    check_deployx_directories,
    check_docker_compose_v2,
    check_docker_daemon,
    check_docker_engine,
    check_git,
    check_python_version,
    check_ubuntu_version,
    run_doctor,
)


def test_doctor_python_version():
    item = check_python_version()
    assert item.status == CheckStatus.OK
    assert "Python 3." in item.details


def test_doctor_ubuntu_version_mock(tmp_path):
    fake_os_release = tmp_path / "os-release"
    fake_os_release.write_text(
        'NAME="Ubuntu"\nVERSION="24.04 LTS (Noble Numbat)"\nID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'
    )
    with patch("deployx.doctor.checks.Path", return_value=fake_os_release):
        item = check_ubuntu_version()
        assert item.status == CheckStatus.OK
        assert "24.04" in item.details


def test_doctor_git_installed(mocker):
    mocker.patch(
        "deployx.doctor.checks.run_command",
        return_value=MagicMock(stdout="git version 2.43.0\n"),
    )
    item = check_git()
    assert item.status == CheckStatus.OK
    assert "git version" in item.details


def test_doctor_docker_compose_missing(mocker):
    from deployx.core.command import CommandError
    mocker.patch(
        "deployx.doctor.checks.run_command",
        side_effect=CommandError("not found", ["docker", "compose"], 1),
    )
    item = check_docker_compose_v2()
    assert item.status == CheckStatus.ERROR
    assert "plugin is missing" in item.details


def test_doctor_docker_daemon_active(mocker):
    mocker.patch(
        "deployx.doctor.checks.run_command",
        return_value=MagicMock(stdout="26.1.1\n"),
    )
    item = check_docker_daemon()
    assert item.status == CheckStatus.OK
    assert "26.1.1" in item.details


def test_doctor_run_summary(mocker):
    # Mock collect_doctor_checks to return predictable list
    from deployx.doctor.checks import DoctorItem
    mock_items = [
        DoctorItem(category="Runtime", name="Python", status=CheckStatus.OK, details="3.12"),
        DoctorItem(category="Storage", name="Disk", status=CheckStatus.OK, details="50GB"),
    ]
    mocker.patch("deployx.doctor.checks.collect_doctor_checks", return_value=mock_items)
    
    test_console = Console(record=True)
    success = run_doctor(test_console)
    assert success is True
    output = test_console.export_text()
    assert "DeployX Doctor Diagnostic Suite" in output
    assert "2 passed" in output
