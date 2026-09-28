"""
Tests for Git operations and SSH Deploy Key management.
"""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from deployx.config import paths, reload_paths
from deployx.core.command import CommandError, CommandResult
from deployx.git.repository import GitRepositoryManager
from deployx.git.ssh import (
    create_deploy_key,
    get_project_ssh_command,
    show_deploy_key,
    verify_deploy_key,
)


@pytest.fixture(autouse=True)
def isolated_deployx(tmp_path, monkeypatch):
    root = tmp_path / "opt_deployx"
    etc = tmp_path / "etc_deployx"
    monkeypatch.setenv("DEPLOYX_ROOT", str(root))
    monkeypatch.setenv("DEPLOYX_CONFIG_DIR", str(etc))
    reload_paths()
    paths.ensure_all_dirs()
    yield


def test_ssh_command_formatting():
    cmd = get_project_ssh_command("myproject")
    assert "IdentitiesOnly=yes" in cmd
    assert "StrictHostKeyChecking=accept-new" in cmd
    assert "myproject" in cmd


def test_create_and_show_deploy_key(mocker):
    # Mock run_command for ssh-keygen to write dummy key files
    def fake_ssh_keygen(cmd, *args, **kwargs):
        # cmd: ['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', '<path>', ...]
        key_path = Path(cmd[6])
        pub_path = Path(f"{cmd[6]}.pub")
        key_path.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nFAKEDATA\n-----END OPENSSH PRIVATE KEY-----")
        pub_path.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGfakekey deployx-demo")
        return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.1)

    mocker.patch("deployx.git.ssh.run_command", side_effect=fake_ssh_keygen)

    key_path = create_deploy_key("demo")
    assert key_path.exists()
    assert (paths.keys_dir / "demo.pub").exists()

    pub_content = show_deploy_key("demo")
    assert pub_content.startswith("ssh-ed25519")
    assert "FAKEDATA" not in pub_content  # Private key is not leaked


def test_verify_deploy_key_success(mocker):
    from deployx.deployment.project import add_project
    from rich.console import Console

    # Add project first
    add_project(
        name="private-app",
        git="git@github.com:example/private.git",
        branch="main",
        private=True,
        domain=None,
        framework="django",
        database="postgres",
        console=Console(record=True),
    )

    # Create dummy key
    key_path = paths.get_project_key_path("private-app")
    key_path.write_text("private-key-data")

    # Mock git ls-remote success
    mocker.patch(
        "deployx.git.ssh.run_command",
        return_value=CommandResult(
            command=["git", "ls-remote"],
            returncode=0,
            stdout="4ba128c704f056d61f1cf01bfbc7c1abf8da4e0f\tHEAD\n",
            stderr="",
            duration=0.5,
        ),
    )

    ok = verify_deploy_key("private-app")
    assert ok is True


def test_git_repository_manager_env():
    pub_mgr = GitRepositoryManager(
        project_name="pub-app",
        repo_url="https://github.com/example/pub.git",
        branch="main",
        private=False,
    )
    assert "GIT_SSH_COMMAND" not in pub_mgr._get_git_env()

    priv_mgr = GitRepositoryManager(
        project_name="priv-app",
        repo_url="git@github.com:example/priv.git",
        branch="main",
        private=True,
    )
    assert "GIT_SSH_COMMAND" in priv_mgr._get_git_env()


def test_git_get_remote_commit(mocker):
    mgr = GitRepositoryManager(
        project_name="test-repo",
        repo_url="https://github.com/example/test.git",
        branch="main",
        private=False,
    )

    mocker.patch(
        "deployx.git.repository.run_command",
        return_value=CommandResult(
            command=["git", "ls-remote"],
            returncode=0,
            stdout="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f\trefs/heads/main\n",
            stderr="",
            duration=0.2,
        ),
    )

    remote_sha = mgr.get_remote_commit()
    assert remote_sha == "f0222384a29ebcd90d80111bfbc7c1abf8da4e0f"
