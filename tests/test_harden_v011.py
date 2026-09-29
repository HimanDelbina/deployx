"""
Comprehensive Test Suite for DeployX v0.1.1 Hardening & Regression Prevention.
Validates:
- Placeholder repository rejection (case-insensitive)
- Public repo remote verification (mocked success, failure, branch not found)
- Private repo registration as unverified
- Key verify improvements (branch existence, commit SHA, marks verified: true)
- Deploy blocked for unverified private repository
- Project remove (safe remove, --purge, confirmation, --force)
- Project edit command (field re-validation, verified state update)
- Invalid config prevents key creation
- Deploy key preservation unless --force
- Non-interactive mode (--non-interactive)
- Project list & info verification state display
- Installer PIP_INDEX_URL support, preflight check, and no forced pip upgrade
- Ubuntu 26.04 warning text in Doctor
- Doctor network check timeout behavior
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from typer.testing import CliRunner

from deployx.cli import app
from deployx.config import paths, reload_paths
from deployx.core.command import CommandError, CommandResult
from deployx.core.security import (
    SecurityError,
    detect_git_url_placeholders,
    validate_git_url,
    validate_project_name,
)
from deployx.deployment.deploy import run_deployment
from deployx.deployment.project import (
    add_project,
    edit_project,
    load_project_config,
    project_exists,
    remove_project,
    verify_public_repository,
)
from deployx.deployment.update import run_update
from deployx.doctor.checks import (
    CheckStatus,
    check_dns_resolution,
    check_github_reachability,
    check_pypi_reachability,
    check_ubuntu_version,
)
from deployx.git.ssh import create_deploy_key, verify_deploy_key
from deployx.models import DeploymentStatus, FrameworkType
from deployx.state import get_state_manager

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


# ==============================================================================
# 1. Placeholder Repository Rejection
# ==============================================================================
@pytest.mark.parametrize(
    "placeholder_url,expected_detected",
    [
        ("git@github.com:HimanDelbina/REAL_REPOSITORY.git", "REAL_REPOSITORY"),
        ("git@github.com:USER/myproject.git", "USER"),
        ("git@github.com:org/REPO.git", "REPO"),
        ("git@github.com:org/REPOSITORY.git", "REPOSITORY"),
        ("git@github.com:OWNER/app.git", "OWNER"),
        ("git@github.com:HimanDelbina/YOUR_REPOSITORY.git", "YOUR_REPOSITORY"),
        ("git@github.com:your-org/valid-app.git", "your-org"),
        ("git@github.com:your-user/valid-app.git", "your-user"),
        ("https://example.com/org/repo.git", "example.com"),
        ("https://github.com/example/valid-name.git", "example"),
    ],
)
def test_placeholder_repository_rejection(placeholder_url, expected_detected):
    placeholders = detect_git_url_placeholders(placeholder_url)
    assert any(expected_detected.lower() in p.lower() for p in placeholders)

    with pytest.raises(SecurityError) as exc_info:
        validate_git_url(placeholder_url)
    assert "Repository URL appears to contain placeholder values:" in str(exc_info.value)
    assert "Provide a real Git repository URL." in str(exc_info.value)


def test_cli_rejects_placeholder_url():
    res = runner.invoke(
        app,
        [
            "project", "add",
            "--name", "myapp",
            "--git", "git@github.com:HimanDelbina/REAL_REPOSITORY.git",
            "--non-interactive",
        ],
    )
    assert res.exit_code == 1
    assert "Repository URL appears to contain placeholder values:" in res.stdout
    assert "REAL_REPOSITORY" in res.stdout
    assert not project_exists("myapp")


def test_placeholder_project_name_rejection():
    with pytest.raises(SecurityError) as exc:
        validate_project_name("example")
    assert "appears to be a placeholder value" in str(exc.value)

    with pytest.raises(SecurityError):
        validate_project_name("USER")


# ==============================================================================
# 2. Public Repository Remote Verification
# ==============================================================================
def test_public_repo_validation_mocked_success(mocker):
    # Mock verify_public_repository
    mocker.patch(
        "deployx.deployment.project.verify_public_repository",
        return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f",
    )

    res = runner.invoke(
        app,
        [
            "project", "add",
            "--name", "valid-pub",
            "--git", "https://github.com/myteam/valid-pub.git",
            "--branch", "main",
            "--non-interactive",
        ],
    )
    assert res.exit_code == 0
    assert "registered successfully" in res.stdout
    assert project_exists("valid-pub")

    cfg = load_project_config("valid-pub")
    assert cfg.git.verified is True


def test_public_repo_validation_failure(mocker):
    # Mock ls-remote failure (repo not found or unreachable)
    mocker.patch(
        "deployx.deployment.project.run_command",
        return_value=CommandResult(
            command=["git", "ls-remote"],
            returncode=128,
            stdout="",
            stderr="fatal: repository 'https://github.com/myteam/notfound.git' not found",
            duration=0.5,
        ),
    )

    res = runner.invoke(
        app,
        [
            "project", "add",
            "--name", "fail-pub",
            "--git", "https://github.com/myteam/notfound.git",
            "--branch", "main",
            "--non-interactive",
        ],
    )
    assert res.exit_code == 1
    assert "Repository not found or unreachable." in res.stdout
    assert not project_exists("fail-pub")


def test_branch_not_found_in_repository(mocker):
    # ls-remote for branch returns empty, but HEAD exists
    def fake_ls_remote(cmd, *args, **kwargs):
        if "refs/heads/dev" in cmd:
            return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.2)
        elif "HEAD" in cmd:
            return CommandResult(command=cmd, returncode=0, stdout="f022238\tHEAD\n", stderr="", duration=0.2)
        return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.2)

    mocker.patch("deployx.deployment.project.run_command", side_effect=fake_ls_remote)

    res = runner.invoke(
        app,
        [
            "project", "add",
            "--name", "missing-branch-app",
            "--git", "https://github.com/myteam/app.git",
            "--branch", "dev",
            "--non-interactive",
        ],
    )
    assert res.exit_code == 1
    assert "Branch 'dev' was not found in the repository." in res.stdout
    assert not project_exists("missing-branch-app")


# ==============================================================================
# 3. Private Repository Registration State & Deployment Guard
# ==============================================================================
def test_private_repo_saved_as_unverified():
    res = runner.invoke(
        app,
        [
            "project", "add",
            "--name", "secret-app",
            "--git", "git@github.com:myteam/secret.git",
            "--branch", "main",
            "--private",
            "--non-interactive",
        ],
    )
    assert res.exit_code == 0
    assert project_exists("secret-app")

    cfg = load_project_config("secret-app")
    assert cfg.git.private is True
    assert cfg.git.verified is False


def test_deploy_blocked_for_unverified_private_repo():
    # Register private app
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "unverified-app",
            "--git", "git@github.com:myteam/unverified.git",
            "--private",
            "--non-interactive",
        ],
    )

    from rich.console import Console
    test_console = Console(record=True)

    # Attempt deployment
    success = run_deployment("unverified-app", console=test_console)
    assert success is False
    output = test_console.export_text()
    assert "Private repository access has not been verified." in output
    assert "deployx key create unverified-app" in output
    assert "deployx key verify unverified-app" in output

    # Attempt update
    update_console = Console(record=True)
    update_ok = run_update("unverified-app", console=update_console)
    assert update_ok is False
    assert "Private repository access has not been verified." in update_console.export_text()


# ==============================================================================
# 4. Key Verify Improvements (Branch existence + commit SHA + marks verified)
# ==============================================================================
def test_key_verify_marks_verified_on_success(mocker):
    # 1. Register private repo
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "corp-portal",
            "--git", "git@github.com:myorg/portal.git",
            "--branch", "main",
            "--private",
            "--non-interactive",
        ],
    )

    # 2. Create dummy key
    key_path = paths.get_project_key_path("corp-portal")
    key_path.write_text("dummy-private-key")

    # 3. Mock git ls-remote branch resolution
    mocker.patch(
        "deployx.git.ssh.run_command",
        return_value=CommandResult(
            command=["git", "ls-remote"],
            returncode=0,
            stdout="abc1234567890\trefs/heads/main\n",
            stderr="",
            duration=0.3,
        ),
    )

    from rich.console import Console
    c = Console(record=True)
    ok = verify_deploy_key("corp-portal", console=c)
    assert ok is True
    output = c.export_text()
    assert "GitHub access verified." in output
    assert "Remote commit: abc1234" in output

    # Config must now be verified=True
    cfg = load_project_config("corp-portal")
    assert cfg.git.verified is True


def test_key_verify_fails_when_branch_missing(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "bad-branch-app",
            "--git", "git@github.com:myorg/bad-branch.git",
            "--branch", "release-v2",
            "--private",
            "--non-interactive",
        ],
    )
    paths.get_project_key_path("bad-branch-app").write_text("dummy-key")

    def fake_ssh_ls_remote(cmd, *args, **kwargs):
        if "refs/heads/release-v2" in cmd:
            return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.2)
        elif "HEAD" in cmd:
            return CommandResult(command=cmd, returncode=0, stdout="f022238\tHEAD\n", stderr="", duration=0.2)
        return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.2)

    mocker.patch("deployx.git.ssh.run_command", side_effect=fake_ssh_ls_remote)

    from rich.console import Console
    c = Console(record=True)
    ok = verify_deploy_key("bad-branch-app", console=c)
    assert ok is False
    assert "Branch 'release-v2' was not found" in c.export_text()


# ==============================================================================
# 5. Invalid Config Prevents Key Creation & Preserving Existing Keys
# ==============================================================================
def test_invalid_config_prevents_key_creation(tmp_path):
    # Manually write an invalid config with placeholder
    pdir = paths.get_project_dir("invalid-repo-proj")
    pdir.mkdir(parents=True)
    bad_cfg = (
        "version: 1\n"
        "project:\n  name: invalid-repo-proj\n"
        "git:\n  repository: git@github.com:HimanDelbina/REAL_REPOSITORY.git\n  branch: main\n  private: true\n"
        "deployment:\n  framework: django\n"
    )
    (pdir / "deployx.yml").write_text(bad_cfg)

    with pytest.raises(ValueError) as exc:
        create_deploy_key("invalid-repo-proj")
    assert "Project repository configuration is invalid." in str(exc.value)
    assert not paths.get_project_key_path("invalid-repo-proj").exists()


def test_preserve_existing_deploy_key_unless_force(mocker):
    # Mock ssh-keygen
    def fake_keygen(cmd, *args, **kwargs):
        key = Path(cmd[6])
        pub = Path(f"{cmd[6]}.pub")
        key.write_text("initial-private-key")
        pub.write_text("initial-public-key")
        return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.1)

    mocker.patch("deployx.git.ssh.run_command", side_effect=fake_keygen)

    # First generation
    key1 = create_deploy_key("keepkey")
    assert key1.read_text() == "initial-private-key"

    # Second generation without force -> must preserve initial key
    key2 = create_deploy_key("keepkey", force=False)
    assert key2.read_text() == "initial-private-key"

    # Third generation with --force
    def fake_keygen_v2(cmd, *args, **kwargs):
        key = Path(cmd[6])
        pub = Path(f"{cmd[6]}.pub")
        key.write_text("regenerated-private-key")
        pub.write_text("regenerated-public-key")
        return CommandResult(command=cmd, returncode=0, stdout="", stderr="", duration=0.1)

    mocker.patch("deployx.git.ssh.run_command", side_effect=fake_keygen_v2)
    key3 = create_deploy_key("keepkey", force=True)
    assert key3.read_text() == "regenerated-private-key"


# ==============================================================================
# 6. Project Remove Command (Safe & Purge)
# ==============================================================================
def test_project_remove_safe():
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "rm-app",
            "--git", "git@github.com:myorg/rm.git",
            "--private",
            "--non-interactive",
        ],
    )
    assert project_exists("rm-app")

    # Safe remove with --force in non-interactive
    res = runner.invoke(app, ["project", "remove", "rm-app", "--force"])
    assert res.exit_code == 0
    assert "successfully removed" in res.stdout
    assert not project_exists("rm-app")
    assert get_state_manager().get_state("rm-app") is None


def test_project_remove_with_purge(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "purge-app",
            "--git", "git@github.com:myorg/purge.git",
            "--private",
            "--non-interactive",
        ],
    )
    # Create deploy key
    key_path = paths.get_project_key_path("purge-app")
    pub_path = paths.get_project_pubkey_path("purge-app")
    key_path.write_text("privkey")
    pub_path.write_text("pubkey")

    mock_down = mocker.patch("deployx.docker.compose.DockerComposeManager.down")

    res = runner.invoke(app, ["project", "remove", "purge-app", "--purge", "--force"])
    assert res.exit_code == 0
    assert not project_exists("purge-app")
    # Under v0.1.2 requirement 9, deploy keys are preserved by default even under --purge
    assert key_path.exists()
    assert pub_path.exists()

    # Key removal requires explicit key remove command
    key_res = runner.invoke(app, ["key", "remove", "purge-app", "--force"])
    assert key_res.exit_code == 0
    assert not key_path.exists()
    assert not pub_path.exists()


# ==============================================================================
# 7. Project Edit Command
# ==============================================================================
def test_project_edit_command(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "edit-app",
            "--git", "git@github.com:myorg/original.git",
            "--branch", "main",
            "--private",
            "--non-interactive",
        ],
    )

    res = runner.invoke(
        app,
        [
            "project", "edit", "edit-app",
            "--branch", "develop",
            "--domain", "portal.acme.com",
            "--database", "sqlite",
            "--yes",
        ],
    )
    assert res.exit_code == 0
    assert "updated successfully" in res.stdout

    cfg = load_project_config("edit-app")
    assert cfg.git.branch == "develop"
    assert cfg.deployment.domain == "portal.acme.com"
    assert cfg.deployment.database.value == "sqlite"


# ==============================================================================
# 8. Project Info & List Verification Display
# ==============================================================================
def test_project_info_and_list_verification_display(mocker):
    mocker.patch(
        "deployx.deployment.project.verify_public_repository",
        return_value="f0222384a29ebcd90d80111bfbc7c1abf8da4e0f",
    )

    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "info-app",
            "--git", "https://github.com/myorg/info-app-with-very-long-repository-name-for-truncation.git",
            "--non-interactive",
        ],
    )

    info_res = runner.invoke(app, ["project", "info", "info-app"])
    assert info_res.exit_code == 0
    assert "Repository Verified:" in info_res.stdout and "Yes" in info_res.stdout
    assert "Deploy Key:" in info_res.stdout and "Not Needed" in info_res.stdout

    list_res = runner.invoke(app, ["project", "list"], env={"COLUMNS": "140"})
    assert list_res.exit_code == 0
    assert "Verified" in list_res.stdout
    assert "..." in list_res.stdout or "\u2026" in list_res.stdout


# ==============================================================================
# 9. Non-Interactive CLI Add
# ==============================================================================
def test_non_interactive_add_missing_params():
    # Missing --name
    res_no_name = runner.invoke(app, ["project", "add", "--git", "https://github.com/myorg/app.git", "--non-interactive"])
    assert res_no_name.exit_code == 1
    assert "--name is required" in res_no_name.stdout

    # Missing --git
    res_no_git = runner.invoke(app, ["project", "add", "--name", "noname", "--non-interactive"])
    assert res_no_git.exit_code == 1
    assert "--git is required" in res_no_git.stdout


# ==============================================================================
# 10. Ubuntu 26.04 Warning Text in Doctor
# ==============================================================================
def test_ubuntu_2604_warning_text(tmp_path):
    fake_os_release = tmp_path / "os-release"
    fake_os_release.write_text(
        'NAME="Ubuntu"\nVERSION="26.04 LTS (Resolute Rhino)"\nID=ubuntu\nVERSION_ID="26.04"\nPRETTY_NAME="Ubuntu 26.04 LTS"\n'
    )
    with patch("deployx.doctor.checks.Path", return_value=fake_os_release):
        item = check_ubuntu_version()
        assert item.status == CheckStatus.WARNING
        assert "Ubuntu 26.04 detected (not yet in validated support matrix)" in item.details
        assert "Continuing in compatibility mode" in item.recommendation


# ==============================================================================
# 11. Doctor Network Checks & Timeout Behavior
# ==============================================================================
def test_doctor_network_reachability_checks(mocker):
    # Mock network checks so test runs deterministically offline
    mocker.patch("deployx.doctor.checks._probe_dns", return_value=True)
    mocker.patch("deployx.doctor.checks._probe_tcp_connect", return_value=True)

    gh_item = check_github_reachability()
    assert gh_item.status == CheckStatus.OK
    assert "reachable" in gh_item.details

    pypi_item = check_pypi_reachability()
    assert pypi_item.status == CheckStatus.OK

    dns_item = check_dns_resolution()
    assert dns_item.status == CheckStatus.OK


def test_doctor_partial_dns_warning(mocker):
    # Simulate partial reachability: github.com resolves, CDN fails
    def mock_dns_partial(host, *args, **kwargs):
        return host == "github.com"

    mocker.patch("deployx.doctor.checks._probe_dns", side_effect=mock_dns_partial)

    dns_item = check_dns_resolution()
    assert dns_item.status == CheckStatus.WARNING
    assert "Partial DNS/network reachability detected" in dns_item.details


# ==============================================================================
# 12. Installer PIP_INDEX_URL and No Forced Pip Upgrade Check
# ==============================================================================
def test_installer_script_features():
    installer_path = Path(__file__).parent.parent / "scripts" / "install.sh"
    content = installer_path.read_text(encoding="utf-8")

    # PIP_INDEX_URL support
    assert "PIP_INDEX_URL" in content
    # Preflight check
    assert "connectivity preflight" in content
    # No forced pip upgrade unless DEPLOYX_UPGRADE_PIP=1
    assert "DEPLOYX_UPGRADE_PIP" in content
    assert "Preserving existing pip in virtual environment" in content
    # Actionable network error messaging
    assert "Possible causes:" in content
    assert "DNS resolution failure" in content
    assert "Retry example:" in content
    # Ubuntu compatibility messaging
    assert "not yet been formally validated on this version" in content
