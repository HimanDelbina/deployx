"""
DeployX Production Environment Generator
Generates .env.production with cryptographically secure credentials,
integrates variables from .env.example if present, and enforces 0600 permissions.
Never touches or commits secrets into the Git repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional

from deployx.config import paths
from deployx.core.filesystem import atomic_write_file, set_secure_permissions
from deployx.core.security import generate_db_password, generate_secure_secret, validate_project_name
from deployx.models import DatabasePreference, ProjectConfig


def parse_env_example(example_path: Optional[Path]) -> Dict[str, str]:
    """Parses key-value pairs and comments from .env.example if available."""
    if not example_path or not example_path.is_file():
        return {}

    parsed: Dict[str, str] = {}
    try:
        content = example_path.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                clean_k = k.strip()
                clean_v = v.strip().strip("'\"")
                if clean_k:
                    parsed[clean_k] = clean_v
    except Exception:
        pass
    return parsed


def generate_production_env(
    project_config: ProjectConfig,
    example_env_path: Optional[Path] = None,
    overwrite: bool = False,
) -> Path:
    """
    Generates /opt/deployx/projects/<project>/.env.production.
    - If .env.production already exists and not overwrite, preserves existing secrets.
    - Uses secrets module for SECRET_KEY and DB password.
    - Sets 0600 file permissions.
    """
    project_name = validate_project_name(project_config.project.name)
    project_dir = paths.get_project_dir(project_name)
    target_env = project_dir / ".env.production"

    if target_env.is_file() and not overwrite:
        # Preserve existing secrets
        return target_env

    # 1. Parse .env.example template
    example_vars = parse_env_example(example_env_path)

    # 2. Cryptographic values
    secret_key = generate_secure_secret(50)
    db_password = generate_db_password(32)
    db_user = f"deployx_{project_name}"[:32]
    db_name = f"deployx_{project_name}"[:32]
    db_host = f"deployx_{project_name}_db"
    db_port = "5432"

    domain = project_config.deployment.domain
    allowed_hosts = f"{domain},localhost,127.0.0.1" if domain else "localhost,127.0.0.1"

    # 3. Base production variables
    env_vars: Dict[str, str] = {
        "DEBUG": "False",
        "SECRET_KEY": secret_key,
        "ALLOWED_HOSTS": allowed_hosts,
    }

    if domain:
        env_vars["CSRF_TRUSTED_ORIGINS"] = f"https://{domain}"

    # Database configuration
    if project_config.deployment.database == DatabasePreference.POSTGRES:
        env_vars["POSTGRES_DB"] = db_name
        env_vars["POSTGRES_USER"] = db_user
        env_vars["POSTGRES_PASSWORD"] = db_password
        env_vars["DATABASE_URL"] = f"postgres://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        env_vars["DB_ENGINE"] = "django.db.backends.postgresql"
        env_vars["DB_NAME"] = db_name
        env_vars["DB_USER"] = db_user
        env_vars["DB_PASSWORD"] = db_password
        env_vars["DB_HOST"] = db_host
        env_vars["DB_PORT"] = db_port

    if project_config.deployment.redis:
        redis_host = f"deployx_{project_name}_redis"
        env_vars["REDIS_URL"] = f"redis://{redis_host}:6379/0"
        env_vars["CELERY_BROKER_URL"] = f"redis://{redis_host}:6379/1"

    # Merge variables from example template if not already handled
    for k, v in example_vars.items():
        if k in env_vars:
            continue
        # Avoid insecure defaults from example template
        if "secret" in k.lower() or "key" in k.lower():
            env_vars[k] = generate_secure_secret(32)
        elif "password" in k.lower():
            env_vars[k] = generate_db_password(24)
        else:
            env_vars[k] = v

    # Format file content
    lines = [
        f"# DeployX Auto-Generated Production Environment for '{project_name}'",
        "# Generated securely with cryptographic secrets.",
        "# DO NOT COMMIT TO VERSION CONTROL.",
        "",
    ]
    for k, v in sorted(env_vars.items()):
        # Escape quotes if needed
        clean_val = str(v).replace('"', '\\"')
        lines.append(f'{k}="{clean_val}"')

    content = "\n".join(lines) + "\n"
    atomic_write_file(target_env, content, mode=0o600)
    set_secure_permissions(target_env, mode=0o600)
    return target_env
