"""
Tests for ProjectConfig and DeploymentState models and serialization.
"""

import pytest
from pydantic import ValidationError

from deployx.models import (
    DatabasePreference,
    DeploymentConfig,
    DeploymentState,
    DeploymentStatus,
    FrameworkType,
    GitConfig,
    HealthStatus,
    ProjectConfig,
    ProjectMeta,
)


def test_project_config_valid():
    cfg = ProjectConfig(
        version=1,
        project=ProjectMeta(name="myproject"),
        git=GitConfig(
            repository="git@github.com:acme-corp/myproject.git",
            branch="main",
            private=True,
        ),
        deployment=DeploymentConfig(
            framework=FrameworkType.DJANGO,
            domain="example.com",
            database=DatabasePreference.POSTGRES,
        ),
    )
    assert cfg.project.name == "myproject"
    assert cfg.git.private is True
    assert cfg.deployment.docker.compose_file == "docker-compose.deployx.yml"

    # Test YAML serialization round-trip
    yaml_str = cfg.to_yaml()
    assert "version: 1" in yaml_str
    assert "name: myproject" in yaml_str

    loaded = ProjectConfig.from_yaml(yaml_str)
    assert loaded.project.name == cfg.project.name
    assert loaded.git.repository == cfg.git.repository
    assert loaded.deployment.framework == FrameworkType.DJANGO


def test_project_config_validation_failures():
    # Invalid project name
    with pytest.raises(ValidationError):
        ProjectConfig(
            version=1,
            project=ProjectMeta(name="../bad-name"),
            git=GitConfig(repository="git@github.com:foo/bar.git"),
        )

    # Invalid git URL
    with pytest.raises(ValidationError):
        ProjectConfig(
            version=1,
            project=ProjectMeta(name="valid-name"),
            git=GitConfig(repository="file:///etc/passwd"),
        )


def test_deployment_state():
    state = DeploymentState.new("proj1", "git@github.com:foo/bar.git", "main")
    assert state.project == "proj1"
    assert state.status == DeploymentStatus.PENDING
    assert state.health_status == HealthStatus.UNKNOWN
    assert state.current_commit is None

    state.current_commit = "4ba128c"
    state.status = DeploymentStatus.HEALTHY
    state.health_status = HealthStatus.HEALTHY
    assert state.current_commit == "4ba128c"
