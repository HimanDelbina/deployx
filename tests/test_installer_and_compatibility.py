"""
Tests for Installer scripts, Python 3.10+ compatibility, and release readiness.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path
import pytest

from deployx.doctor.checks import CheckStatus, check_python_version


def test_doctor_python_version_matrix(mocker):
    # Test Python 3.10 (Ubuntu 22.04 LTS standard)
    mocker.patch.object(sys, "version_info", (3, 10, 14, "final", 0))
    item = check_python_version()
    assert item.status == CheckStatus.OK
    assert ">= 3.10" in item.details

    # Test Python 3.12 (Ubuntu 24.04 LTS standard)
    mocker.patch.object(sys, "version_info", (3, 12, 3, "final", 0))
    item12 = check_python_version()
    assert item12.status == CheckStatus.OK

    # Test Python 3.9 (Below minimum) -> ERROR
    mocker.patch.object(sys, "version_info", (3, 9, 18, "final", 0))
    item9 = check_python_version()
    assert item9.status == CheckStatus.ERROR
    assert "Upgrade to Python 3.10" in item9.recommendation


def test_no_pypi_fallback_in_internal_installer():
    """Verify scripts/install.sh NEVER falls back to blind PyPI 'pip install deployx'."""
    script_path = Path(__file__).parent.parent / "scripts" / "install.sh"
    content = script_path.read_text(encoding="utf-8")

    # It must not execute 'pip install deployx'
    assert "pip install deployx" not in content
    # It must enforce local checked-out source
    assert "pyproject.toml" in content
    assert '"${VENV_DIR}/bin/pip" install "${REPO_ROOT}"' in content


def test_root_bootstrap_installer_architecture():
    """Verify root install.sh structure, official repo source, mktemp, and trap."""
    root_script = Path(__file__).parent.parent / "install.sh"
    assert root_script.is_file()
    content = root_script.read_text(encoding="utf-8")

    # Official repository URL
    assert "https://github.com/HimanDelbina/deployx.git" in content
    # Trap for cleanup
    assert "trap cleanup EXIT INT TERM" in content
    # Temporary directory with mktemp
    assert "mktemp" in content
    # Invokes internal installer
    assert "scripts/install.sh" in content
    # No dangerous eval
    assert "eval " not in content


def test_bash_syntax_if_available():
    """Validates shell syntax with bash -n if a bash binary is present."""
    base_dir = Path(__file__).parent.parent
    root_installer = base_dir / "install.sh"
    internal_installer = base_dir / "scripts" / "install.sh"

    # Search for bash
    bash_path = None
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.is_file():
        bash_path = str(git_bash)
    else:
        import shutil
        bash_path = shutil.which("bash")

    if not bash_path:
        pytest.skip("bash executable not found on host to run syntax check")

    # Check root installer
    res1 = subprocess.run([bash_path, "-n", str(root_installer)], capture_output=True, text=True)
    assert res1.returncode == 0, f"Syntax error in install.sh: {res1.stderr}"

    # Check internal installer
    res2 = subprocess.run([bash_path, "-n", str(internal_installer)], capture_output=True, text=True)
    assert res2.returncode == 0, f"Syntax error in scripts/install.sh: {res2.stderr}"


def test_python_ast_compatibility():
    """Verifies that all Python modules in deployx compile cleanly."""
    base_dir = Path(__file__).parent.parent / "deployx"
    for py_file in base_dir.rglob("*.py"):
        code = py_file.read_text(encoding="utf-8")
        # Compile to AST
        tree = ast.parse(code, filename=str(py_file))
        assert tree is not None
