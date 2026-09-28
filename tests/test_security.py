"""
Tests for DeployX Security module.
Covers project name validation, Git URL sanitization, path traversal prevention,
and secret redaction.
"""

from pathlib import Path
import pytest

from deployx.core.security import (
    SecurityError,
    generate_db_password,
    generate_secure_secret,
    prevent_path_traversal,
    redact_sensitive_text,
    validate_domain,
    validate_git_url,
    validate_project_name,
)


def test_validate_project_name_valid():
    assert validate_project_name("myproject") == "myproject"
    assert validate_project_name("my-app_v2") == "my-app_v2"
    assert validate_project_name("proj123") == "proj123"


def test_validate_project_name_invalid():
    # Empty
    with pytest.raises(SecurityError):
        validate_project_name("")
    
    # Path traversal and injection
    with pytest.raises(SecurityError):
        validate_project_name("../etc")
    with pytest.raises(SecurityError):
        validate_project_name("proj;rm -rf /")
    with pytest.raises(SecurityError):
        validate_project_name("proj name")
    with pytest.raises(SecurityError):
        validate_project_name("/absolute/path")
    with pytest.raises(SecurityError):
        validate_project_name("-flag")
    with pytest.raises(SecurityError):
        validate_project_name("..")
    with pytest.raises(SecurityError):
        validate_project_name("a" * 65)


def test_validate_git_url_valid():
    assert validate_git_url("git@github.com:octocat/Hello-World.git") == "git@github.com:octocat/Hello-World.git"
    assert validate_git_url("https://github.com/octocat/Hello-World.git") == "https://github.com/octocat/Hello-World.git"
    assert validate_git_url("ssh://git@github.com:22/octocat/Hello-World.git") == "ssh://git@github.com:22/octocat/Hello-World.git"


def test_validate_git_url_invalid():
    # Option injection
    with pytest.raises(SecurityError, match="cannot start with a dash"):
        validate_git_url("--upload-pack=evil")

    # Forbidden protocols
    with pytest.raises(SecurityError, match="Forbidden Git URL protocol"):
        validate_git_url("file:///etc/passwd")
    with pytest.raises(SecurityError, match="Forbidden Git URL protocol"):
        validate_git_url("ext::sh -c evil")
    
    # Empty or malformed
    with pytest.raises(SecurityError):
        validate_git_url("")
    with pytest.raises(SecurityError):
        validate_git_url("not a url")


def test_validate_domain():
    assert validate_domain(None) is None
    assert validate_domain("example.com") == "example.com"
    assert validate_domain("api.my-domain.org") == "api.my-domain.org"
    assert validate_domain("localhost") == "localhost"

    with pytest.raises(SecurityError):
        validate_domain("invalid domain name")
    with pytest.raises(SecurityError):
        validate_domain("http://example.com")


def test_prevent_path_traversal(tmp_path):
    base_dir = tmp_path / "projects"
    base_dir.mkdir()

    # Valid subpath
    valid_subpath = base_dir / "myproject"
    resolved = prevent_path_traversal(base_dir, valid_subpath)
    assert resolved == valid_subpath.resolve()

    # Traversal attempt
    traversal = base_dir / ".." / "secret.txt"
    with pytest.raises(SecurityError, match="Path traversal detected"):
        prevent_path_traversal(base_dir, traversal)


def test_generate_crypto_secrets():
    s1 = generate_secure_secret(32)
    s2 = generate_secure_secret(32)
    assert len(s1) > 20
    assert s1 != s2

    p1 = generate_db_password(24)
    p2 = generate_db_password(24)
    assert len(p1) == 24
    assert p1 != p2


def test_redact_sensitive_text():
    # Redact private key
    fake_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABlwAAAAdzc2gtcn\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    text = f"Connecting using key:\n{fake_key}\nDone."
    redacted = redact_sensitive_text(text)
    assert "b3BlbnNzaC" not in redacted
    assert "[REDACTED_PRIVATE_KEY]" in redacted

    # Redact secret assignments
    sample = "POSTGRES_PASSWORD='SuperSecretPassword123' and SECRET_KEY: xyz987"
    redacted2 = redact_sensitive_text(sample)
    assert "SuperSecretPassword123" not in redacted2
    assert "POSTGRES_PASSWORD=[REDACTED]" in redacted2

    # Redact custom extra secrets
    custom = "User token is secret_api_token_abc456 inside text"
    redacted3 = redact_sensitive_text(custom, extra_secrets=["secret_api_token_abc456"])
    assert "secret_api_token_abc456" not in redacted3
    assert "[REDACTED]" in redacted3
