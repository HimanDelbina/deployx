"""
DeployX SSH Deploy Key Manager
Generates per-project ED25519 keys, applies strict 0600 permissions,
provides public key exports, and verifies GitHub repository access without cloning.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from deployx.config import paths
from deployx.core.command import CommandError, run_command
from deployx.core.filesystem import ensure_directory, set_secure_permissions
from deployx.core.security import validate_project_name


def get_project_ssh_command(project_name: str) -> str:
    """
    Constructs the secure GIT_SSH_COMMAND environment variable for a project:
    - Points to dedicated per-project private key
    - Enforces IdentitiesOnly=yes to avoid sending other keys
    - Uses StrictHostKeyChecking=accept-new for safe known_hosts handling
    """
    valid_name = validate_project_name(project_name)
    key_path = paths.get_project_key_path(valid_name)
    # Ensure forward slashes for cross-platform compatibility in GIT_SSH_COMMAND
    key_posix = key_path.as_posix()
    return f'ssh -i "{key_posix}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'


def create_deploy_key(
    project_name: str,
    force: bool = False,
    console: Optional[Console] = None,
) -> Path:
    """
    Generates a dedicated ED25519 SSH deploy key for the project.
    Permissions:
    - /opt/deployx/keys: 0700
    - Private key: 0600
    - Public key: 0644
    """
    valid_name = validate_project_name(project_name)
    ensure_directory(paths.keys_dir, mode=0o700)

    key_path = paths.get_project_key_path(valid_name)
    pub_path = paths.get_project_pubkey_path(valid_name)

    if key_path.exists() and not force:
        msg = (
            f"Deploy key already exists for '{valid_name}' at {key_path}.\n"
            "Use --force to regenerate (this will invalidate GitHub access until updated)."
        )
        if console:
            console.print(f"[bold yellow]Warning:[/bold yellow] {msg}")
        return key_path

    if key_path.exists():
        key_path.unlink(missing_ok=True)
    if pub_path.exists():
        pub_path.unlink(missing_ok=True)

    # Generate ED25519 key without passphrase
    cmd = [
        "ssh-keygen",
        "-t", "ed25519",
        "-N", "",
        "-f", str(key_path),
        "-C", f"deployx-{valid_name}",
    ]

    try:
        run_command(cmd, timeout=30)
    except CommandError as exc:
        if console:
            console.print(f"[bold red]Failed to generate SSH key:[/bold red] {exc}")
        raise

    # Enforce strict file permissions
    set_secure_permissions(key_path, mode=0o600)
    set_secure_permissions(pub_path, mode=0o644)

    if console:
        console.print(
            Panel(
                f"[bold green]SSH Deploy Key generated successfully for '{valid_name}'![/bold green]\n\n"
                f"[bold]Private Key:[/bold] {key_path} (Permissions: 0600)\n"
                f"[bold]Public Key:[/bold]  {pub_path}\n\n"
                f"Run [bold cyan]deployx key show {valid_name}[/bold cyan] to view the public key.",
                title="Deploy Key Created",
                border_style="green",
            )
        )

    return key_path


def show_deploy_key(project_name: str, console: Optional[Console] = None) -> str:
    """
    Reads and displays the public deploy key for adding to GitHub.
    Never exposes or logs the private key.
    """
    valid_name = validate_project_name(project_name)
    pub_path = paths.get_project_pubkey_path(valid_name)

    if not pub_path.is_file():
        err_msg = (
            f"No deploy key found for project '{valid_name}'.\n"
            f"Generate one first with: deployx key create {valid_name}"
        )
        if console:
            console.print(f"[bold red]Error:[/bold red] {err_msg}")
        raise FileNotFoundError(err_msg)

    pub_key_content = pub_path.read_text(encoding="utf-8").strip()

    if console:
        instructions = (
            "[bold cyan]Add this Deploy Key to your GitHub repository:[/bold cyan]\n"
            "1. Open your repository on GitHub\n"
            "2. Navigate to: [bold]Settings[/bold] -> [bold]Deploy keys[/bold]\n"
            "3. Click [bold]Add deploy key[/bold]\n"
            f"4. Title: [bold]DeployX ({valid_name})[/bold]\n"
            "5. Paste the key below into the 'Key' field (Leave 'Allow write access' unchecked):\n\n"
            f"[bold green]{pub_key_content}[/bold green]\n\n"
            f"After adding, verify access with: [bold cyan]deployx key verify {valid_name}[/bold cyan]"
        )
        console.print(Panel(instructions, title=f"GitHub Deploy Key: {valid_name}", border_style="cyan"))

    return pub_key_content


def verify_deploy_key(project_name: str, console: Optional[Console] = None) -> bool:
    """
    Verifies that the SSH deploy key has authenticated read access to the GitHub repo.
    Uses 'git ls-remote <repo> HEAD' with GIT_SSH_COMMAND to test access without cloning.
    """
    from deployx.deployment.project import load_project_config

    valid_name = validate_project_name(project_name)
    cfg = load_project_config(valid_name)
    repo_url = cfg.git.repository

    key_path = paths.get_project_key_path(valid_name)
    if not key_path.is_file():
        msg = f"SSH key not found at {key_path}. Create it first with: deployx key create {valid_name}"
        if console:
            console.print(f"[bold red]Error:[/bold red] {msg}")
        return False

    ssh_cmd = get_project_ssh_command(valid_name)
    test_env = {"GIT_SSH_COMMAND": ssh_cmd}

    if console:
        console.print(f"[cyan]Testing SSH deploy key access to remote repository...[/cyan]")
        console.print(f"[dim]Repository: {repo_url}[/dim]")

    cmd = ["git", "ls-remote", repo_url, "HEAD"]
    try:
        res = run_command(cmd, env=test_env, timeout=30)
        output = res.stdout.strip()
        if output:
            remote_sha = output.split()[0]
            if console:
                console.print(
                    Panel(
                        f"[bold green]Deploy key verified successfully![/bold green]\n\n"
                        f"[bold]Remote Repository:[/bold] {repo_url}\n"
                        f"[bold]Remote HEAD Commit:[/bold] {remote_sha}\n"
                        f"[bold]Access Status:[/bold]      Authenticated & Read-Ready",
                        title="Key Verification Success",
                        border_style="green",
                    )
                )
            return True
        else:
            if console:
                console.print("[bold yellow]Verification inconclusive: Remote returned empty response.[/bold yellow]")
            return False
    except CommandError as exc:
        if console:
            console.print(
                Panel(
                    f"[bold red]Deploy key verification failed![/bold red]\n\n"
                    f"{exc}\n\n"
                    f"[bold yellow]Troubleshooting:[/bold yellow]\n"
                    f"1. Make sure you copied the public key using: [bold cyan]deployx key show {valid_name}[/bold cyan]\n"
                    "2. Check that the key is registered in your GitHub repository Deploy Keys.\n"
                    "3. Ensure the repository URL in deployx.yml is accurate.",
                    title="Key Verification Failed",
                    border_style="red",
                )
            )
        return False
