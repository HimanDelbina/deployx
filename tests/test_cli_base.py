"""
Base CLI tests for DeployX.
"""

from typer.testing import CliRunner
from deployx.cli import app
from deployx import __version__

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.stdout
    assert "deploy" in result.stdout
    assert "update" in result.stdout
    assert "status" in result.stdout
    assert "project" in result.stdout
    assert "key" in result.stdout
