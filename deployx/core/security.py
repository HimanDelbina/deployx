"""
DeployX Security Module
Provides strict validation, path traversal guards, cryptographic secret generators,
and comprehensive secret redaction.
"""

from __future__ import annotations

import re
import secrets
import string
from pathlib import Path


class SecurityError(ValueError):
    """Raised when a security validation or constraint is violated."""
    pass


PROJECT_NAME_REGEX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,62}$")
DOMAIN_REGEX = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$|^localhost$"
)

# Standard Git URLs: SSH (git@github.com:org/repo.git) or HTTPS (https://github.com/org/repo.git)
GIT_SSH_REGEX = re.compile(
    r"^(?:ssh:\/\/)?(?:[a-zA-Z0-9_\-\.]+@)?[a-zA-Z0-9\.\-]+(?::[0-9]+)?[:\/][a-zA-Z0-9_.\-\/]+(?:\.git)?$"
)
GIT_HTTPS_REGEX = re.compile(
    r"^https:\/\/[a-zA-Z0-9\.\-]+(?::[0-9]+)?\/[a-zA-Z0-9_.\-\/]+(?:\.git)?$"
)

# Patterns for sensitive data redaction
PRIVATE_KEY_REGEX = re.compile(
    r"-----BEGIN [A-Z0-9_ -]+?KEY-----[\s\S]+?-----END [A-Z0-9_ -]+?KEY-----"
)
SECRET_ASSIGNMENT_REGEX = re.compile(
    r"(?i)\b([a-z0-9_]*(?:PASSWORD|SECRET|API_KEY|SECRET_KEY|PRIVATE_KEY|TOKEN|CREDENTIAL)[a-z0-9_]*)\s*[:=]\s*(['\"]?)([^\s'\";\n]+)\2"
)


def validate_project_name(name: str) -> str:
    """
    Validates project name against safe naming rules:
    - 2 to 63 characters
    - Must start with an alphanumeric character
    - May contain alphanumeric characters, underscores, and hyphens
    - Prevents command injection and path traversal
    """
    if not isinstance(name, str):
        raise SecurityError("Project name must be a string.")
    
    clean_name = name.strip()
    if not clean_name:
        raise SecurityError("Project name cannot be empty.")
    
    if not PROJECT_NAME_REGEX.match(clean_name):
        raise SecurityError(
            f"Invalid project name '{name}'. Must be 2-63 chars, start with a letter or digit, "
            "and only contain letters, digits, dashes, and underscores."
        )
    
    # Explicit check for reserved words or path symbols
    if clean_name in {".", "..", "root", "default", "system"}:
        raise SecurityError(f"Project name '{name}' is reserved and cannot be used.")
    
    return clean_name


def validate_git_url(url: str) -> str:
    """
    Validates Git repository URL:
    - Rejects strings starting with '-' to block CLI argument injection.
    - Rejects dangerous schemes like file://, ext::, fd::.
    - Requires standard SSH or HTTPS Git URLs.
    """
    if not isinstance(url, str):
        raise SecurityError("Git URL must be a string.")
    
    clean_url = url.strip()
    if not clean_url:
        raise SecurityError("Git URL cannot be empty.")
    
    # Prevent option injection
    if clean_url.startswith("-"):
        raise SecurityError(f"Git URL cannot start with a dash: '{clean_url}'")
    
    # Check for forbidden dangerous protocols
    forbidden_prefixes = ("file://", "ext::", "fd::", "ftp://", "gopher://")
    for prefix in forbidden_prefixes:
        if clean_url.lower().startswith(prefix):
            raise SecurityError(f"Forbidden Git URL protocol '{prefix}' in '{clean_url}'")
    
    if not (GIT_SSH_REGEX.match(clean_url) or GIT_HTTPS_REGEX.match(clean_url)):
        raise SecurityError(
            f"Invalid Git URL format: '{clean_url}'. Must be valid SSH (e.g. git@github.com:user/repo.git) "
            "or HTTPS (e.g. https://github.com/user/repo.git)."
        )
    
    return clean_url


def validate_domain(domain: str | None) -> str | None:
    """
    Validates domain name if provided.
    Allows standard FQDN or 'localhost', or None.
    """
    if domain is None:
        return None
    
    clean_domain = domain.strip().lower()
    if not clean_domain:
        return None
    
    if not DOMAIN_REGEX.match(clean_domain):
        raise SecurityError(f"Invalid domain name: '{domain}'. Must be a valid FQDN or localhost.")
    
    return clean_domain


def prevent_path_traversal(base_dir: Path | str, target_path: Path | str) -> Path:
    """
    Ensures target_path resolves strictly within base_dir.
    Prevents path traversal attacks (e.g. ../../etc/passwd).
    """
    base = Path(base_dir).resolve()
    target = Path(target_path).resolve()
    
    try:
        target.relative_to(base)
    except ValueError:
        raise SecurityError(
            f"Path traversal detected! Target path '{target}' is outside base directory '{base}'."
        )
    
    return target


def generate_secure_secret(length: int = 50) -> str:
    """
    Generates a cryptographically strong secret token (e.g. for Django SECRET_KEY).
    """
    return secrets.token_urlsafe(length)


def generate_db_password(length: int = 32) -> str:
    """
    Generates a strong random database password using letters and digits.
    Avoids tricky shell quote characters while maintaining high entropy.
    """
    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def redact_sensitive_text(text: str, extra_secrets: list[str] | None = None) -> str:
    """
    Redacts private keys, known secrets, and password assignments from text/logs.
    """
    if not text:
        return text
    
    # 1. Redact PEM/SSH private keys
    redacted = PRIVATE_KEY_REGEX.sub("[REDACTED_PRIVATE_KEY]", text)
    
    # 2. Redact key-value secrets
    def _replace_secret_assignment(match: re.Match) -> str:
        key = match.group(1)
        return f"{key}=[REDACTED]"
    
    redacted = SECRET_ASSIGNMENT_REGEX.sub(_replace_secret_assignment, redacted)
    
    # 3. Redact any explicitly registered secrets
    if extra_secrets:
        for secret_val in extra_secrets:
            if secret_val and len(secret_val) >= 4:
                redacted = redacted.replace(secret_val, "[REDACTED]")
    
    return redacted
