"""
DeployX Production Deployment Pipeline
Executes complete 12-stage automated deployment workflow:
Preflight -> Sync Git -> Commit Resolve -> Detect Framework -> Validate/Generate Config ->
Build Docker Image -> Spin up Infra (DB/Redis) -> In-container Migrations ->
In-container Collectstatic -> Start Application -> Health Check -> Persist State.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

import os
from deployx.config import paths
from deployx.core.command import CommandError, run_command
from deployx.core.filesystem import ensure_directory
from deployx.core.security import redact_url_credentials, validate_project_name
from deployx.deployment.health import perform_http_healthcheck
from deployx.deployment.project import load_project_config
from deployx.detectors.registry import detect_repository
from deployx.docker.compose import DockerComposeManager
from deployx.generators.django import (
    generate_compose_file,
    generate_django_dockerfile,
    is_deployx_generated_file,
)
from deployx.generators.environment import generate_production_env
from deployx.git.repository import GitRepositoryManager
from deployx.logging.logger import ProjectLogger
from deployx.models import (
    DatabasePreference,
    DeploymentState,
    DeploymentStatus,
    HealthStatus,
    ProjectConfig,
)
from deployx.state import get_state_manager
from deployx.ui.progress import DeploymentProgressReporter


def resolve_build_environment(config: ProjectConfig) -> dict[str, str]:
    """
    Resolves build environment variables with priority:
    os.environ > project config (build.python.*) > default (omitted/empty)
    """
    resolved: dict[str, str] = {}
    py_build = getattr(getattr(config, "build", None), "python", None)

    # PIP_INDEX_URL
    if "PIP_INDEX_URL" in os.environ and os.environ["PIP_INDEX_URL"]:
        resolved["PIP_INDEX_URL"] = os.environ["PIP_INDEX_URL"]
    elif py_build and py_build.index_url:
        resolved["PIP_INDEX_URL"] = py_build.index_url

    # PIP_EXTRA_INDEX_URL
    if "PIP_EXTRA_INDEX_URL" in os.environ and os.environ["PIP_EXTRA_INDEX_URL"]:
        resolved["PIP_EXTRA_INDEX_URL"] = os.environ["PIP_EXTRA_INDEX_URL"]
    elif py_build and py_build.extra_index_url:
        resolved["PIP_EXTRA_INDEX_URL"] = py_build.extra_index_url

    # PIP_TRUSTED_HOST
    if "PIP_TRUSTED_HOST" in os.environ and os.environ["PIP_TRUSTED_HOST"]:
        resolved["PIP_TRUSTED_HOST"] = os.environ["PIP_TRUSTED_HOST"]
    elif py_build and py_build.trusted_host:
        resolved["PIP_TRUSTED_HOST"] = py_build.trusted_host

    return resolved


def check_package_index_reachability(index_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """
    Performs a lightweight reachability check from the host to a custom Python package index.
    Non-blocking: failures emit diagnostics and warnings but do not abort deployment.
    """
    import urllib.error
    import urllib.request
    safe_url = redact_url_credentials(index_url.strip())
    try:
        req = urllib.request.Request(
            index_url.strip(),
            headers={"User-Agent": "DeployX-Precheck"},
            method="HEAD",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"Reachable (status {resp.status})"
    except urllib.error.HTTPError as exc:
        return True, f"Host reached (HTTP {exc.code})"
    except Exception as exc:
        return False, str(exc)


def run_deployment(
    project_name: str,
    target_commit: Optional[str] = None,
    verbose: bool = False,
    plain: bool = False,
    regenerate: bool = False,
    console: Optional[Console] = None,
) -> bool:
    """
    Executes the production deployment pipeline for a registered project.
    """
    valid_name = validate_project_name(project_name)
    logger = ProjectLogger(valid_name)
    state_mgr = get_state_manager()
    current_state = state_mgr.get_state(valid_name)

    log_dir = paths.get_project_log_dir(valid_name)
    ensure_directory(log_dir, mode=0o750)
    deploy_log_file = log_dir / "deploy.log"

    reporter = DeploymentProgressReporter(
        project_name=valid_name,
        total_stages=12,
        verbose=verbose,
        plain=plain,
        console=console,
    )

    if console:
        console.print(f"\n[bold cyan]Starting Deployment Pipeline for '{valid_name}'[/bold cyan]")

    logger.info(f"=== Starting Deployment Pipeline for '{valid_name}' ===")

    try:
        # Stage 1: Preflight checks & load configuration
        reporter.start_stage(1, "Preflight checks & load configuration")
        try:
            config: ProjectConfig = load_project_config(valid_name)
            build_env = resolve_build_environment(config)
            pip_idx = build_env.get("PIP_INDEX_URL")
            reporter.config = config
            reporter.python_index = pip_idx
        except Exception as exc:
            from deployx.core.security import format_validation_error
            msg = (
                f"[bold red]Project configuration for '{valid_name}' is invalid:[/bold red]\n\n"
                f"  {format_validation_error(exc)}\n\n"
                f"[bold yellow]To repair this project:[/bold yellow]\n"
                f"  [bold cyan]deployx project edit {valid_name} --git <valid_repository_url>[/bold cyan]\n\n"
                f"[bold yellow]To inspect configuration:[/bold yellow]\n"
                f"  [bold cyan]deployx project info {valid_name}[/bold cyan]"
            )
            if console:
                console.print(
                    Panel(
                        msg,
                        title=f"Deployment Blocked: {valid_name}",
                        border_style="red",
                    )
                )
            logger.error(f"Configuration validation failed for '{valid_name}': {exc}")
            return False

        # Check SSH key and verification state if private
        if config.git.private:
            if not getattr(config.git, "verified", False):
                msg = (
                    "Private repository access has not been verified.\n\n"
                    "Run:\n"
                    f"deployx key create {valid_name}\n"
                    f"deployx key show {valid_name}\n"
                    f"deployx key verify {valid_name}"
                )
                if console:
                    console.print(f"[bold red]{msg}[/bold red]")
                logger.error(msg)
                return False

            key_path = paths.get_project_key_path(valid_name)
            if not key_path.is_file():
                raise CommandError(
                    f"Private repository deploy key missing at {key_path}",
                    command=[],
                    returncode=1,
                    actionable_advice=f"Generate deploy key with: deployx key create {valid_name}",
                )

        # Stage 2: Synchronize repository
        reporter.start_stage(2, f"Syncing repository ({config.git.branch})")
        logger.info(f"Fetching repository: {config.git.repository} (branch: {config.git.branch})")

        repo_mgr = GitRepositoryManager(
            project_name=valid_name,
            repo_url=config.git.repository,
            branch=config.git.branch,
            private=config.git.private,
        )
        resolved_commit = repo_mgr.sync_repository()
        if target_commit:
            repo_mgr.checkout_commit(target_commit)
            resolved_commit = target_commit

        short_commit = resolved_commit[:7]
        prev_commit = current_state.current_commit if current_state else None

        # Stage 3: Resolve commit & image version tagging
        image_tag = f"deployx_{valid_name}:{short_commit}"
        reporter.start_stage(3, f"Resolved commit: {short_commit} (Image: {image_tag})")
        logger.info(f"Target commit: {resolved_commit} -> Image tag: {image_tag}")

        # Stage 4: Analyze project codebase & infrastructure
        reporter.start_stage(4, "Analyzing project and framework components")
        detection = detect_repository(repo_mgr.repo_dir)
        logger.info(
            f"Framework detected: {detection.framework.value} (confidence: {detection.confidence:.2f})"
        )

        # Stage 5: Validate configuration & protect existing repo files
        reporter.start_stage(5, "Validating deployment configuration")
        project_dir = paths.get_project_dir(valid_name)

        # Stage 6: Generate missing deployment configurations
        reporter.start_stage(6, "Generating isolated deployment configurations")

        # 6a. Production environment (.env.production)
        env_file = generate_production_env(
            config,
            example_env_path=detection.infrastructure.env_example_path,
            overwrite=False,
        )
        logger.info(f"Verified environment configuration at {env_file}")

        # 6b. Dockerfile (only if repository lacks one)
        if detection.infrastructure.has_dockerfile:
            logger.info(
                f"Preserving existing repository Dockerfile at {detection.infrastructure.dockerfile_path}"
            )
        else:
            df_path = project_dir / "Dockerfile.deployx"
            if df_path.is_file():
                if is_deployx_generated_file(df_path) or regenerate:
                    generate_django_dockerfile(config, detection, df_path)
                    logger.info(f"Refreshed DeployX Dockerfile at {df_path}")
                else:
                    logger.info(
                        f"Preserving user-owned Dockerfile at {df_path} (missing DeployX marker)"
                    )
            else:
                generate_django_dockerfile(config, detection, df_path)
                logger.info(f"Generated Dockerfile at {df_path}")

        # 6c. Compose file (docker-compose.deployx.yml)
        compose_file = project_dir / "docker-compose.deployx.yml"
        if compose_file.is_file():
            if is_deployx_generated_file(compose_file) or regenerate:
                generate_compose_file(config, detection, image_tag, compose_file)
                logger.info(f"Refreshed DeployX Docker Compose manifest at {compose_file}")
            else:
                logger.info(
                    f"Preserving user-owned Compose file at {compose_file} (missing DeployX marker)"
                )
        else:
            generate_compose_file(config, detection, image_tag, compose_file)
            logger.info(f"Generated Docker Compose v2 manifest at {compose_file}")

        compose_mgr = DockerComposeManager(valid_name, compose_file=compose_file)

        # Stage 7: Docker build
        reporter.start_stage(7, f"Building Docker image ({image_tag})")

        # Optional lightweight reachability check for custom package index
        if pip_idx:
            reachable, reason = check_package_index_reachability(pip_idx)
            safe_idx = redact_url_credentials(pip_idx)
            if reachable:
                logger.info(f"Package index precheck passed: {safe_idx} ({reason})")
            else:
                logger.warning(
                    f"Host precheck warning: Custom package index '{safe_idx}' could not be reached from host: {reason}"
                )
                if console:
                    console.print(
                        f"[dim yellow]Notice: Host precheck could not reach '{safe_idx}' ({reason}). Proceeding with build...[/dim yellow]"
                    )

        logger.info(f"Executing: docker compose build web")
        compose_mgr.build(
            service="web",
            stream=True,
            on_line=reporter.on_build_output,
            on_heartbeat=reporter.on_heartbeat,
            on_stall=reporter.on_stall,
            log_file=deploy_log_file,
            env=build_env,
        )

        # Stage 8: Spin up supporting infrastructure (PostgreSQL / Redis)
        infra_services = []
        if config.deployment.database == DatabasePreference.POSTGRES or detection.infrastructure.has_postgres:
            infra_services.append("db")
        if config.deployment.redis or detection.infrastructure.has_redis:
            infra_services.append("redis")

        infra_label = f"Starting infrastructure services ({', '.join(infra_services)})" if infra_services else "Checking infrastructure services"
        reporter.start_stage(8, infra_label)
        if infra_services:
            logger.info(f"Starting infrastructure services: {infra_services}")
            compose_mgr.up(services=infra_services)

        # Stage 9: Execute database migrations inside container
        reporter.start_stage(9, "Running database migrations in container")
        logger.info("Executing migrations: python manage.py migrate --noinput")
        try:
            compose_mgr.run_transient("web", ["python", "manage.py", "migrate", "--noinput"])
        except CommandError as mig_exc:
            logger.warn(f"Migration command warning or non-critical skip: {mig_exc.message}")

        # Stage 10: Collect static files inside container
        reporter.start_stage(10, "Collecting static files in container")
        logger.info("Executing collectstatic: python manage.py collectstatic --noinput")
        try:
            compose_mgr.run_transient("web", ["python", "manage.py", "collectstatic", "--noinput"])
        except CommandError as cs_exc:
            logger.warn(f"Collectstatic warning or skip: {cs_exc.message}")

        # Stage 11: Start main web application
        reporter.start_stage(11, "Starting application containers")
        logger.info("Starting web container: docker compose up -d web")
        compose_mgr.up(services=["web"])

        # Stage 12: Health check & state persistence
        reporter.start_stage(12, "Performing application health check")

        health_ok = True
        if config.deployment.healthcheck.enabled:
            health_ok = perform_http_healthcheck(
                port=config.deployment.docker.port,
                path=config.deployment.healthcheck.path,
                timeout=config.deployment.healthcheck.timeout,
                retries=config.deployment.healthcheck.retries,
                interval=config.deployment.healthcheck.interval,
                console=console,
            )

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        final_status = DeploymentStatus.HEALTHY if health_ok else DeploymentStatus.UNHEALTHY
        final_health = HealthStatus.HEALTHY if health_ok else HealthStatus.UNHEALTHY

        new_state = DeploymentState(
            project=valid_name,
            repository=config.git.repository,
            branch=config.git.branch,
            current_commit=resolved_commit,
            previous_commit=prev_commit,
            deployed_at=now_iso,
            docker_image=image_tag,
            status=final_status,
            health_status=final_health,
            last_error=None if health_ok else "Healthcheck failed or timed out.",
        )
        state_mgr.save_state(new_state)

        if not health_ok:
            logger.error("Deployment completed but healthcheck failed.")
            if console:
                console.print(
                    Panel(
                        f"[bold yellow]Deployment completed with warnings:[/bold yellow]\n\n"
                        f"Containers are running, but health check failed at port {config.deployment.docker.port}.\n"
                        f"Inspect logs using: [bold cyan]deployx logs {valid_name}[/bold cyan]",
                        title="Deployment Warning",
                        border_style="yellow",
                    )
                )
            return False

        logger.info(f"Deployment successfully completed for commit {short_commit}")
        if console:
            console.print(
                Panel(
                    f"[bold green]Deployment successfully completed for '{valid_name}'![/bold green]\n\n"
                    f"[bold]Deployed Commit:[/bold] {short_commit}\n"
                    f"[bold]Docker Image:[/bold]    {image_tag}\n"
                    f"[bold]Health Status:[/bold]   {final_health.value}\n"
                    f"[bold]Endpoint:[/bold]        http://127.0.0.1:{config.deployment.docker.port}{config.deployment.healthcheck.path}\n"
                    f"[bold]Timestamp:[/bold]       {now_iso}",
                    title="DeployX Success",
                    border_style="green",
                )
            )
        return True

    except KeyboardInterrupt:
        logger.warn(f"Deployment cancelled by user (Ctrl+C) for '{valid_name}'")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        fail_state = current_state or DeploymentState.new(valid_name, "", "main")
        fail_state.status = DeploymentStatus.FAILED
        fail_state.last_error = "Deployment cancelled by user"
        state_mgr.save_state(fail_state)

        if console:
            console.print("\n[bold yellow]Deployment cancelled by user.[/bold yellow]")
        return False

    except CommandError as exc:
        logger.error(f"Deployment command failed: {exc}")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        fail_state = current_state or DeploymentState.new(valid_name, "", "main")
        fail_state.status = DeploymentStatus.FAILED
        fail_state.last_error = str(exc)
        state_mgr.save_state(fail_state)

        reporter.show_failure_summary(
            exit_code=exc.returncode,
            exception_msg=exc.message + (f"\n{exc.actionable_advice}" if exc.actionable_advice else ""),
        )
        return False

    except Exception as exc:
        logger.error(f"Deployment failed: {exc}")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        fail_state = current_state or DeploymentState.new(valid_name, "", "main")
        fail_state.status = DeploymentStatus.FAILED
        fail_state.last_error = str(exc)
        state_mgr.save_state(fail_state)

        reporter.show_failure_summary(
            exit_code=1,
            exception_msg=str(exc),
        )
        return False
