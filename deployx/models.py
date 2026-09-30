"""
DeployX Pydantic Models & Schemas
Enforces strict schema validation for project configurations (deployx.yml)
and deployment state persistence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
import yaml
from pydantic import BaseModel, Field, field_validator

from deployx.core.security import validate_domain, validate_git_url, validate_project_name


class FrameworkType(str, Enum):
    DJANGO = "django"
    FASTAPI = "fastapi"
    FLASK = "flask"
    NODE = "node"
    CUSTOM = "custom"


class DatabasePreference(str, Enum):
    POSTGRES = "postgres"
    SQLITE = "sqlite"
    MYSQL = "mysql"
    NONE = "none"


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    BUILDING = "building"
    MIGRATING = "migrating"
    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    FAILED = "failed"
    STOPPED = "stopped"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    DISABLED = "disabled"


class ProjectMeta(BaseModel):
    name: str
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_project_name(v)


class GitConfig(BaseModel):
    repository: str
    branch: str = "main"
    private: bool = False
    verified: bool = False

    @field_validator("repository")
    @classmethod
    def validate_repo(cls, v: str) -> str:
        return validate_git_url(v)

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, v: str) -> str:
        v = v.strip()
        if not v or v.startswith("-") or ".." in v:
            raise ValueError(f"Invalid branch name '{v}'")
        return v


class HealthcheckConfig(BaseModel):
    enabled: bool = True
    path: str = "/"
    port: int = 8000
    timeout: int = 10
    retries: int = 6
    interval: int = 5
    expected_status: int = 200


class DockerConfig(BaseModel):
    compose_file: str = "docker-compose.deployx.yml"
    service_name: str = "web"
    port: int = 8000


class DeploymentConfig(BaseModel):
    framework: FrameworkType = FrameworkType.DJANGO
    domain: str | None = None
    database: DatabasePreference = DatabasePreference.POSTGRES
    redis: bool = False
    celery: bool = False
    docker: DockerConfig = Field(default_factory=DockerConfig)
    healthcheck: HealthcheckConfig = Field(default_factory=HealthcheckConfig)
    build_timeout: int | None = 3600
    stall_warning_after: int = 120
    heartbeat_interval: int = 25

    @field_validator("domain")
    @classmethod
    def validate_dom(cls, v: str | None) -> str | None:
        return validate_domain(v)


class PythonBuildConfig(BaseModel):
    index_url: str | None = None
    extra_index_url: str | None = None
    trusted_host: str | None = None

    @field_validator("index_url", "extra_index_url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        if v is not None:
            clean = v.strip()
            if not clean:
                return None
            if not (clean.startswith("http://") or clean.startswith("https://")):
                raise ValueError("Package index URL must start with 'http://' or 'https://'")
            return clean
        return v

    @field_validator("trusted_host")
    @classmethod
    def validate_host(cls, v: str | None) -> str | None:
        if v is not None:
            clean = v.strip()
            return clean if clean else None
        return v


class AptBuildConfig(BaseModel):
    mirror_url: str | None = None


class BuildConfig(BaseModel):
    python: PythonBuildConfig = Field(default_factory=PythonBuildConfig)
    apt: AptBuildConfig = Field(default_factory=AptBuildConfig)


class ProjectConfig(BaseModel):
    """
    Schema for /opt/deployx/projects/<name>/deployx.yml
    Versioned configuration format.
    """
    version: int = 1
    project: ProjectMeta
    git: GitConfig
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)
    build: BuildConfig = Field(default_factory=BuildConfig)

    def to_yaml(self) -> str:
        data = self.model_dump(mode="json")
        return yaml.dump(data, sort_keys=False, default_flow_style=False)

    @classmethod
    def from_yaml(cls, content: str) -> ProjectConfig:
        raw_data = yaml.safe_load(content)
        if not isinstance(raw_data, dict):
            raise ValueError("Invalid YAML: Root structure must be a dictionary.")
        return cls.model_validate(raw_data)


class DeploymentState(BaseModel):
    """
    Schema for persistent deployment tracking (/opt/deployx/state/<name>.state.json)
    """
    project: str
    repository: str
    branch: str
    current_commit: str | None = None
    previous_commit: str | None = None
    deployed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    docker_image: str | None = None
    previous_image: str | None = None
    status: DeploymentStatus = DeploymentStatus.PENDING
    health_status: HealthStatus = HealthStatus.UNKNOWN
    last_error: str | None = None
    failed_stage: str | None = None
    exit_code: int | None = None
    timeout_reason: str | None = None
    last_build_step: str | None = None
    last_health_response: str | None = None
    last_exception_summary: str | None = None
    detected_runtime_database: str | None = None

    @classmethod
    def new(cls, project: str, repository: str, branch: str) -> DeploymentState:
        now_iso = datetime.now(timezone.utc).isoformat()
        return cls(
            project=project,
            repository=repository,
            branch=branch,
            deployed_at=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
