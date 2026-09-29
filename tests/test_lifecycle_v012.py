"""
Comprehensive Test Suite for DeployX v0.1.2 Project Lifecycle Management:
- Project Edit (git, branch, domain, private/public, atomic validation)
- Verification Invalidation & Transition Handling
- Project Remove (Safe unregister, Purge runtime resources, Destructive volume deletion)
- Project Rename (Pre-deployment safe rename, Deployed project blocking, Key migration)
- Security & Path Traversal Guards
- Non-Interactive Safety & Backward Compatibility
"""

import shutil
from pathlib import Path
import pytest
from typer.testing import CliRunner

from deployx.cli import app
from deployx.config import paths, reload_paths
from deployx.core.security import SecurityError, validate_project_name
from deployx.deployment.project import load_project_config, project_exists
from deployx.models import DeploymentConfig, DeploymentState, DeploymentStatus, ProjectConfig
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
# 1. Project Edit Tests
# ==============================================================================
def test_project_edit_repository(mocker):
    mocker.patch(
        "deployx.deployment.project.verify_public_repository",
        return_value="abc1234567890abcdef1234567890abcdef12345",
    )
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "edit-repo-app",
            "--git", "https://github.com/myorg/oldrepo.git",
            "--non-interactive",
        ],
    )
    res = runner.invoke(
        app,
        [
            "project", "edit", "edit-repo-app",
            "--git", "https://github.com/myorg/newrepo.git",
            "--yes",
        ],
    )
    assert res.exit_code == 0
    cfg = load_project_config("edit-repo-app")
    assert cfg.git.repository == "https://github.com/myorg/newrepo.git"
    assert cfg.git.verified is True


def test_project_edit_branch(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "edit-branch-app",
            "--git", "git@github.com:myorg/app.git",
            "--private",
            "--non-interactive",
        ],
    )
    res = runner.invoke(
        app,
        [
            "project", "edit", "edit-branch-app",
            "--branch", "feature-x",
            "--yes",
        ],
    )
    assert res.exit_code == 0
    cfg = load_project_config("edit-branch-app")
    assert cfg.git.branch == "feature-x"
    assert cfg.git.verified is False


def test_project_edit_domain(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "edit-dom-app",
            "--git", "git@github.com:myorg/domapp.git",
            "--private",
            "--non-interactive",
        ],
    )
    # Set custom domain
    res = runner.invoke(
        app,
        [
            "project", "edit", "edit-dom-app",
            "--domain", "portal.example.org",
            "--yes",
        ],
    )
    assert res.exit_code == 0
    cfg = load_project_config("edit-dom-app")
    assert cfg.deployment.domain == "portal.example.org"

    # Remove domain with --no-domain
    res_clear = runner.invoke(
        app,
        [
            "project", "edit", "edit-dom-app",
            "--no-domain",
            "--yes",
        ],
    )
    assert res_clear.exit_code == 0
    cfg_cleared = load_project_config("edit-dom-app")
    assert cfg_cleared.deployment.domain is None


def test_project_edit_private_public_transition(mocker):
    mocker.patch(
        "deployx.deployment.project.verify_public_repository",
        return_value="abc1234567890abcdef1234567890abcdef12345",
    )
    # Start as Public
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "trans-app",
            "--git", "https://github.com/myorg/transapp.git",
            "--non-interactive",
        ],
    )
    cfg = load_project_config("trans-app")
    assert cfg.git.private is False
    assert cfg.git.verified is True

    # Transition Public -> Private
    res_priv = runner.invoke(
        app,
        [
            "project", "edit", "trans-app",
            "--private",
            "--yes",
        ],
    )
    assert res_priv.exit_code == 0
    cfg_priv = load_project_config("trans-app")
    assert cfg_priv.git.private is True
    assert cfg_priv.git.verified is False
    assert "Previous repository verification is no longer valid" in res_priv.stdout
    assert "deployx key verify trans-app" in res_priv.stdout

    # Create dummy deploy key
    key_path = paths.get_project_key_path("trans-app")
    key_path.write_text("privkey-content")

    # Transition Private -> Public
    res_pub = runner.invoke(
        app,
        [
            "project", "edit", "trans-app",
            "--public",
            "--yes",
        ],
    )
    assert res_pub.exit_code == 0
    cfg_pub = load_project_config("trans-app")
    assert cfg_pub.git.private is False
    assert cfg_pub.git.verified is True
    # Key must NOT be deleted automatically
    assert key_path.exists()
    assert "Existing deploy key preserved" in res_pub.stdout


def test_edit_validation_failure_preserves_old_config(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "fail-edit-app",
            "--git", "git@github.com:myorg/stable.git",
            "--branch", "main",
            "--private",
            "--non-interactive",
        ],
    )
    # Attempt edit with placeholder repository
    res = runner.invoke(
        app,
        [
            "project", "edit", "fail-edit-app",
            "--git", "git@github.com:USER/app.git",
            "--yes",
        ],
    )
    assert res.exit_code != 0
    assert "placeholder" in res.stdout.lower()

    # Verify old config was untouched
    cfg = load_project_config("fail-edit-app")
    assert cfg.git.repository == "git@github.com:myorg/stable.git"
    assert cfg.git.branch == "main"


def test_repository_and_branch_change_invalidates_verification(mocker):
    # Setup project with verified=True
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "inval-app",
            "--git", "git@github.com:myorg/inval.git",
            "--private",
            "--non-interactive",
        ],
    )
    cfg = load_project_config("inval-app")
    cfg.git.verified = True
    paths.get_project_config_path("inval-app").write_text(cfg.to_yaml())

    # Branch change must invalidate verification
    res = runner.invoke(
        app,
        [
            "project", "edit", "inval-app",
            "--branch", "hotfix-branch",
            "--yes",
        ],
    )
    assert res.exit_code == 0
    cfg_updated = load_project_config("inval-app")
    assert cfg_updated.git.branch == "hotfix-branch"
    assert cfg_updated.git.verified is False
    assert "Previous repository verification is no longer valid" in res.stdout


# ==============================================================================
# 2. Duplicate Project Protection
# ==============================================================================
def test_duplicate_project_rejection(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "dup-app",
            "--git", "git@github.com:myorg/dup.git",
            "--private",
            "--non-interactive",
        ],
    )
    res = runner.invoke(
        app,
        [
            "project", "add",
            "--name", "dup-app",
            "--git", "git@github.com:myorg/dup.git",
            "--private",
            "--non-interactive",
        ],
    )
    assert res.exit_code != 0
    assert "already exists" in res.stdout
    assert "deployx project edit dup-app" in res.stdout


# ==============================================================================
# 3. Project Remove Tests
# ==============================================================================
def test_safe_project_remove_preserves_volumes_backups_keys(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "safe-del-app",
            "--git", "git@github.com:myorg/del.git",
            "--private",
            "--non-interactive",
        ],
    )
    key_path = paths.get_project_key_path("safe-del-app")
    pub_path = paths.get_project_pubkey_path("safe-del-app")
    key_path.write_text("privkey-safe")
    pub_path.write_text("pubkey-safe")

    backup_file = paths.backups_dir / "safe-del-app_2026.tar.gz"
    backup_file.write_text("archive")

    res = runner.invoke(app, ["project", "remove", "safe-del-app", "--force"])
    assert res.exit_code == 0
    assert not project_exists("safe-del-app")

    # Keys and backups must be preserved
    assert key_path.exists()
    assert pub_path.exists()
    assert backup_file.exists()
    assert "Deploy key preserved at" in res.stdout


def test_purge_runtime_resources_does_not_delete_volumes_or_keys(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "purge-safe-app",
            "--git", "git@github.com:myorg/purge-app.git",
            "--private",
            "--non-interactive",
        ],
    )
    key_path = paths.get_project_key_path("purge-safe-app")
    key_path.write_text("privkey-purge")

    mock_down = mocker.patch("deployx.docker.compose.DockerComposeManager.down")
    compose_file = paths.get_project_dir("purge-safe-app") / "docker-compose.deployx.yml"
    compose_file.write_text("services: {}")

    res = runner.invoke(app, ["project", "remove", "purge-safe-app", "--purge", "--force"])
    assert res.exit_code == 0

    # Compose down must be called with volumes=False!
    mock_down.assert_called_once_with(volumes=False)
    # Key must NOT be deleted
    assert key_path.exists()


def test_delete_volumes_requires_explicit_confirmation(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "vol-del-app",
            "--git", "git@github.com:myorg/voldel.git",
            "--private",
            "--non-interactive",
        ],
    )
    compose_file = paths.get_project_dir("vol-del-app") / "docker-compose.deployx.yml"
    compose_file.write_text("services: {}")
    mock_down = mocker.patch("deployx.docker.compose.DockerComposeManager.down")

    # Non-interactive without --yes must fail!
    res_no_yes = runner.invoke(
        app,
        [
            "project", "remove", "vol-del-app",
            "--purge", "--delete-volumes",
            "--non-interactive",
        ],
    )
    assert res_no_yes.exit_code != 0
    assert "Destructive volume deletion" in res_no_yes.stdout
    assert "--yes" in res_no_yes.stdout
    mock_down.assert_not_called()

    # Non-interactive with --yes must succeed and call down(volumes=True)
    res_yes = runner.invoke(
        app,
        [
            "project", "remove", "vol-del-app",
            "--purge", "--delete-volumes",
            "--non-interactive",
            "--yes",
        ],
    )
    assert res_yes.exit_code == 0
    mock_down.assert_called_once_with(volumes=True)


# ==============================================================================
# 4. Project Rename Tests
# ==============================================================================
def test_rename_undeployed_project(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "alpha-app",
            "--git", "git@github.com:myorg/alpha.git",
            "--private",
            "--non-interactive",
        ],
    )
    key_path = paths.get_project_key_path("alpha-app")
    pub_path = paths.get_project_pubkey_path("alpha-app")
    key_path.write_text("secret-ed25519-key")
    pub_path.write_text("public-ed25519-key")

    res = runner.invoke(app, ["project", "rename", "alpha-app", "beta-app", "--yes"])
    assert res.exit_code == 0
    assert not project_exists("alpha-app")
    assert project_exists("beta-app")

    # Verify config updated
    cfg = load_project_config("beta-app")
    assert cfg.project.name == "beta-app"

    # Verify state updated
    state = get_state_manager().get_state("beta-app")
    assert state is not None
    assert state.project == "beta-app"
    assert get_state_manager().get_state("alpha-app") is None

    # Verify keys migrated with same content
    new_key = paths.get_project_key_path("beta-app")
    new_pub = paths.get_project_pubkey_path("beta-app")
    assert new_key.is_file() and new_key.read_text() == "secret-ed25519-key"
    assert new_pub.is_file() and new_pub.read_text() == "public-ed25519-key"
    assert not key_path.exists()


def test_rename_duplicate_target_rejection(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "p1-app",
            "--git", "git@github.com:myorg/p1.git",
            "--private",
            "--non-interactive",
        ],
    )
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "p2-app",
            "--git", "git@github.com:myorg/p2.git",
            "--private",
            "--non-interactive",
        ],
    )
    res = runner.invoke(app, ["project", "rename", "p1-app", "p2-app", "--yes"])
    assert res.exit_code != 0
    assert "already exists" in res.stdout


def test_rename_deployed_project_rejection(mocker):
    runner.invoke(
        app,
        [
            "project", "add",
            "--name", "dep-app",
            "--git", "git@github.com:myorg/dep.git",
            "--private",
            "--non-interactive",
        ],
    )
    state = get_state_manager().get_state("dep-app")
    state.status = DeploymentStatus.HEALTHY
    state.current_commit = "9cd0841"
    get_state_manager().save_state(state)

    res = runner.invoke(app, ["project", "rename", "dep-app", "new-dep-app", "--yes"])
    assert res.exit_code != 0
    assert "supported only before first deployment" in res.stdout
    assert project_exists("dep-app")
    assert not project_exists("new-dep-app")


# ==============================================================================
# 5. Security & Path Traversal Validation
# ==============================================================================
@pytest.mark.parametrize(
    "invalid_name",
    [
        "../app",
        "../../etc",
        "/app",
        ".",
        "..",
        "~/myproject",
        "app/nested",
        "app\\nested",
        "app..traversal",
        "root",
        "keys",
        "backups",
        "logs",
        "system",
        "config",
        "USER",
        "REPO",
        "REAL_REPOSITORY",
    ],
)
def test_path_traversal_project_names_rejected(invalid_name):
    with pytest.raises(SecurityError):
        validate_project_name(invalid_name)


# ==============================================================================
# 6. Backward Compatibility with v0.1.0 Config
# ==============================================================================
def test_v010_config_backward_compatibility():
    legacy_yaml = """
version: 1
project:
  name: legacy-app
git:
  repository: git@github.com:myorg/legacy.git
  branch: main
  private: true
deployment:
  framework: django
  database: postgres
"""
    cfg = ProjectConfig.from_yaml(legacy_yaml)
    assert cfg.project.name == "legacy-app"
    assert getattr(cfg.git, "verified", False) is False
    assert getattr(cfg.project, "created_at", None) is None
    assert getattr(cfg.project, "updated_at", None) is None


# ==============================================================================
# 7. Key Remove Command
# ==============================================================================
def test_deploy_key_remove_command():
    key_path = paths.get_project_key_path("test-key-app")
    pub_path = paths.get_project_pubkey_path("test-key-app")
    key_path.write_text("privkey-val")
    pub_path.write_text("pubkey-val")

    res = runner.invoke(app, ["key", "remove", "test-key-app", "--force"])
    assert res.exit_code == 0
    assert not key_path.exists()
    assert not pub_path.exists()
    assert "successfully removed" in res.stdout
