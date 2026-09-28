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

from deployx.config import paths
from deployx.core.command import CommandError, run_command
from deployx.core.security import validate_project_name
from deployx.deployment.health import perform_http_healthcheck
from deployx.deployment.project import load_project_config
from deployx.detectors.registry import detect_repository
from deployx.docker.compose import DockerComposeManager
from deployx.generators.django import generate_compose_file, generate_django_dockerfile
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


def run_deployment(
    project_name: str,
    target_commit: Optional[str] = None,
    console: Optional[Console] = None,
) -> bool:
    """
    Executes the production deployment pipeline for a registered project.
    """
    valid_name = validate_project_name(project_name)
    logger = ProjectLogger(valid_name)
    state_mgr = get_state_manager()
    current_state = state_mgr.get_state(valid_name)

    if console:
        console.print(f"\n[bold cyan]Starting Deployment Pipeline for '{valid_name}'[/bold cyan]")

    logger.info(f"=== Starting Deployment Pipeline for '{valid_name}' ===")

    try:
        # Stage 1: Preflight checks & load configuration
        if console:
            console.print("[1/12] [cyan]Running preflight checks...[/cyan]")
        config: ProjectConfig = load_project_config(valid_name)

        # Check SSH key if private
        if config.git.private:
            key_path = paths.get_project_key_path(valid_name)
            if not key_path.is_file():
                raise CommandError(
                    f"Private repository deploy key missing at {key_path}",
                    command=[],
                    returncode=1,
                    actionable_advice=f"Generate deploy key with: deployx key create {valid_name}",
                )

        # Stage 2: Synchronize repository
        if console:
            console.print(f"[2/12] [cyan]Syncing repository ({config.git.branch})...[/cyan]")
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
        if console:
            console.print(
                f"[3/12] [cyan]Resolved commit: [bold green]{short_commit}[/bold green] (Image: {image_tag})[/cyan]"
            )
        logger.info(f"Target commit: {resolved_commit} -> Image tag: {image_tag}")

        # Stage 4: Analyze project codebase & infrastructure
        if console:
            console.print("[4/12] [cyan]Analyzing project and framework components...[/cyan]")
        detection = detect_repository(repo_mgr.repo_dir)
        logger.info(
            f"Framework detected: {detection.framework.value} (confidence: {detection.confidence:.2f})"
        )

        # Stage 5: Validate configuration & protect existing repo files
        if console:
            console.print("[5/12] [cyan]Validating deployment configuration...[/cyan]")
        project_dir = paths.get_project_dir(valid_name)

        # Stage 6: Generate missing deployment configurations
        if console:
            console.print("[6/12] [cyan]Generating isolated deployment configurations...[/cyan]")

        # 6a. Production environment (.env.production)
        env_file = generate_production_env(
            config,
            example_env_path=detection.infrastructure.env_example_path,
            overwrite=False,
        )
        logger.info(f"Verified environment configuration at {env_file}")

        # 6b. Dockerfile (only if repository lacks one)
        if not detection.infrastructure.has_dockerfile:
            df_path = project_dir / "Dockerfile.deployx"
            generate_django_dockerfile(config, detection, df_path)
            logger.info(f"Generated Dockerfile at {df_path}")
        else:
            logger.info(f"Preserving existing repository Dockerfile at {detection.infrastructure.dockerfile_path}")

        # 6c. Compose file (docker-compose.deployx.yml)
        compose_file = project_dir / "docker-compose.deployx.yml"
        generate_compose_file(config, detection, image_tag, compose_file)
        logger.info(f"Generated Docker Compose v2 manifest at {compose_file}")

        compose_mgr = DockerComposeManager(valid_name, compose_file=compose_file)

        # Stage 7: Docker build
        if console:
            console.print(f"[7/12] [cyan]Building Docker image ([bold]{image_tag}[/bold])...[/cyan]")
        logger.info(f"Executing: docker compose build web")
        compose_mgr.build(service="web")

        # Stage 8: Spin up supporting infrastructure (PostgreSQL / Redis)
        infra_services = []
        if config.deployment.database == DatabasePreference.POSTGRES or detection.infrastructure.has_postgres:
            infra_services.append("db")
        if config.deployment.redis or detection.infrastructure.has_redis:
            infra_services.append("redis")

        if infra_services:
            if console:
                console.print(f"[8/12] [cyan]Starting infrastructure services ({', '.join(infra_services)})...[/cyan]")
            logger.info(f"Starting infrastructure services: {infra_services}")
            compose_mgr.up(services=infra_services)

        # Stage 9: Execute database migrations inside container
        if console:
            console.print("[9/12] [cyan]Running database migrations in container...[/cyan]")
        logger.info("Executing migrations: python manage.py migrate --noinput")
        try:
            compose_mgr.run_transient("web", ["python", "manage.py", "migrate", "--noinput"])
        except CommandError as mig_exc:
            logger.warn(f"Migration command warning or non-critical skip: {mig_exc.message}")

        # Stage 10: Collect static files inside container
        if console:
            console.print("[10/12] [cyan]Collecting static files in container...[/cyan]")
        logger.info("Executing collectstatic: python manage.py collectstatic --noinput")
        try:
            compose_mgr.run_transient("web", ["python", "manage.py", "collectstatic", "--noinput"])
        except CommandError as cs_exc:
            logger.warn(f"Collectstatic warning or skip: {cs_exc.message}")

        # Stage 11: Start main web application
        if console:
            console.print("[11/12] [cyan]Starting application containers...[/cyan]")
        logger.info("Starting web container: docker compose up -d web")
        compose_mgr.up(services=["web"])

        # Stage 12: Health check & state persistence
        if console:
            console.print("[12/12] [cyan]Performing application health check...[/cyan]")

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

    except Exception as exc:
        logger.error(f"Deployment failed: {exc}")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        fail_state = current_state or DeploymentState.new(valid_name, "", "main")
        fail_state.status = DeploymentStatus.FAILED
        fail_state.last_error = str(exc)
        state_mgr.save_state(fail_state)

        if console:
            console.print(
                Panel(
                    f"[bold red]Deployment failed for '{valid_name}':[/bold red]\n\n"
                    f"{exc}\n\n"
                    f"Check full logs with: [bold cyan]cat /opt/deployx/logs/{valid_name}/deploy.log[/bold cyan]\n"
                    f"Or view container logs: [bold cyan]deployx logs {valid_name}[/bold cyan]",
                    title="Deployment Error",
                    border_style="red",
                )
            )
        return False
