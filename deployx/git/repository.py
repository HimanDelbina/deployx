"""
DeployX Git Repository Manager
Handles safe cloning, remote branch fetching, commit resolution,
and detached checkouts for both public and private repositories.
Avoids blind pulling to ensure exact commit reproducibility.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from deployx.config import paths
from deployx.core.command import CommandError, CommandResult, run_command
from deployx.core.filesystem import ensure_directory
from deployx.core.security import validate_git_url, validate_project_name
from deployx.git.ssh import get_project_ssh_command


class GitRepositoryManager:
    """Manages local Git lifecycle for a registered DeployX project."""

    def __init__(
        self,
        project_name: str,
        repo_url: str,
        branch: str = "main",
        private: bool = False,
    ):
        self.project_name = validate_project_name(project_name)
        self.repo_url = validate_git_url(repo_url)
        self.branch = branch.strip()
        self.private = private
        self.project_dir = paths.get_project_dir(self.project_name)
        self.repo_dir = self.project_dir / "repo"

    def _get_git_env(self) -> Dict[str, str]:
        """Injects per-project SSH credentials if repository is private."""
        env = {}
        if self.private:
            env["GIT_SSH_COMMAND"] = get_project_ssh_command(self.project_name)
        return env

    def is_cloned(self) -> bool:
        """Checks if repository has been cloned."""
        return (self.repo_dir / ".git").is_dir()

    def get_current_commit(self) -> Optional[str]:
        """Returns the current HEAD commit SHA if repo is cloned, else None."""
        if not self.is_cloned():
            return None
        res = run_command(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            env=self._get_git_env(),
            check=True,
        )
        return res.stdout.strip()

    def get_remote_commit(self) -> str:
        """
        Inspects the remote repository branch and resolves its latest commit SHA
        without performing a full clone or merge.
        """
        cmd = ["git", "ls-remote", self.repo_url, f"refs/heads/{self.branch}"]
        res = run_command(cmd, env=self._get_git_env(), check=True, timeout=45)
        lines = res.stdout.strip().splitlines()
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == f"refs/heads/{self.branch}":
                return parts[0]

        # Fallback to checking HEAD if branch was not found under refs/heads/
        cmd_head = ["git", "ls-remote", self.repo_url, "HEAD"]
        res_head = run_command(cmd_head, env=self._get_git_env(), check=True, timeout=45)
        head_lines = res_head.stdout.strip().splitlines()
        if head_lines:
            return head_lines[0].split()[0]

        raise CommandError(
            f"Could not resolve commit for branch '{self.branch}' on remote {self.repo_url}",
            cmd=cmd,
            returncode=1,
            actionable_advice=f"Verify that branch '{self.branch}' exists in the remote repository.",
        )

    def sync_repository(self) -> str:
        """
        Synchronizes the repository:
        - Clones if not present.
        - Fetches configured branch and resets hard to origin/<branch>.
        Returns the resolved commit SHA.
        """
        ensure_directory(self.project_dir, mode=0o750)
        env = self._get_git_env()

        if not self.is_cloned():
            # Clone specific branch
            cmd_clone = [
                "git", "clone",
                "--branch", self.branch,
                "--single-branch",
                self.repo_url,
                str(self.repo_dir),
            ]
            run_command(cmd_clone, env=env, timeout=180)
        else:
            # Sync existing clone
            run_command(["git", "remote", "set-url", "origin", self.repo_url], cwd=self.repo_dir, env=env)
            run_command(["git", "fetch", "origin", self.branch], cwd=self.repo_dir, env=env, timeout=120)
            run_command(["git", "checkout", self.branch], cwd=self.repo_dir, env=env)
            run_command(["git", "reset", "--hard", f"origin/{self.branch}"], cwd=self.repo_dir, env=env)

        current_sha = self.get_current_commit()
        if not current_sha:
            raise RuntimeError(f"Failed to resolve current commit for '{self.project_name}' after sync.")
        return current_sha

    def checkout_commit(self, sha: str) -> None:
        """Checks out a specific commit SHA in detached HEAD state."""
        if not self.is_cloned():
            raise FileNotFoundError(f"Repository not cloned at {self.repo_dir}")
        run_command(
            ["git", "checkout", sha.strip()],
            cwd=self.repo_dir,
            env=self._get_git_env(),
            check=True,
        )

    def get_commit_summary(self, sha: str) -> str:
        """Gets short 1-line commit subject message."""
        if not self.is_cloned():
            return ""
        try:
            res = run_command(
                ["git", "log", "-1", "--format=%s", sha.strip()],
                cwd=self.repo_dir,
                env=self._get_git_env(),
                check=False,
            )
            return res.stdout.strip()
        except Exception:
            return ""


def detect_remote_default_branch(
    repo_url: str,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Optional[str]:
    """
    Inspects remote repository HEAD to automatically discover the default branch (e.g. master, main, trunk).
    Uses 'git ls-remote --symref <repo> HEAD' or heads inspection.
    """
    import re

    # 1. Try git ls-remote --symref <repo_url> HEAD
    try:
        res = run_command(
            ["git", "ls-remote", "--symref", repo_url, "HEAD"],
            env=env,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                # Matches: ref: refs/heads/master\tHEAD
                m = re.search(r"ref:\s*refs/heads/(\S+)\s+HEAD", line)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass

    # 2. Try git ls-remote --heads <repo_url>
    branches = get_remote_branches(repo_url, env=env, timeout=timeout)
    if not branches:
        return None

    # Priority check
    for preferred in ["main", "master", "trunk", "production"]:
        if preferred in branches:
            return preferred

    return branches[0]


def get_remote_branches(
    repo_url: str,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> List[str]:
    """
    Queries all available branches on the remote repository.
    """
    try:
        res = run_command(
            ["git", "ls-remote", "--heads", repo_url],
            env=env,
            timeout=timeout,
            check=False,
        )
        if res.returncode != 0:
            return []

        branches: List[str] = []
        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1].startswith("refs/heads/"):
                branch_name = parts[1].removeprefix("refs/heads/").strip()
                if branch_name:
                    branches.append(branch_name)
        return branches
    except Exception:
        return []

