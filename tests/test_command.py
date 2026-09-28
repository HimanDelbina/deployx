"""
Tests for DeployX Secure Subprocess Command Runner.
"""

import sys
import pytest

from deployx.core.command import (
    CommandError,
    diagnose_command_failure,
    run_command,
)


def test_run_command_success():
    # Use python executable to run a cross-platform command
    res = run_command([sys.executable, "-c", "print('hello world')"])
    assert res.success
    assert res.returncode == 0
    assert "hello world" in res.stdout
    assert res.duration >= 0


def test_run_command_failure_raises_command_error():
    with pytest.raises(CommandError) as exc_info:
        run_command([sys.executable, "-c", "import sys; sys.exit(42)"], check=True)
    
    err = exc_info.value
    assert err.returncode == 42
    assert "exit code 42" in str(err)


def test_run_command_check_false():
    res = run_command([sys.executable, "-c", "import sys; sys.exit(7)"], check=False)
    assert not res.success
    assert res.returncode == 7


def test_run_command_timeout():
    with pytest.raises(CommandError) as exc_info:
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    
    assert "timed out after 1 seconds" in str(exc_info.value)


def test_run_command_not_found():
    with pytest.raises(CommandError) as exc_info:
        run_command(["non_existent_binary_xyz_12345"])
    
    assert exc_info.value.returncode == 127
    assert "not found on system PATH" in str(exc_info.value)


def test_run_command_secret_masking():
    super_secret = "UltraSecretToken999888"
    with pytest.raises(CommandError) as exc_info:
        run_command(
            [sys.executable, "-c", f"import sys; sys.stderr.write('{super_secret}'); sys.exit(1)"],
            sensitive_strings=[super_secret],
        )
    
    assert super_secret not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_diagnose_command_failure():
    # Git publickey error
    advice = diagnose_command_failure(
        ["git", "clone", "git@github.com:foo/bar.git"],
        128,
        "Permission denied (publickey). fatal: Could not read from remote repository.",
        "",
    )
    assert advice is not None
    assert "Deploy Key has been added" in advice

    # Docker daemon not running
    advice2 = diagnose_command_failure(
        ["docker", "compose", "up"],
        1,
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
        "",
    )
    assert advice2 is not None
    assert "sudo systemctl start docker" in advice2
