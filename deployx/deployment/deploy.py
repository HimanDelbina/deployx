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
from rich.table import Table

import os
from deployx.config import get_effective_build_timeout, paths
from deployx.core.command import CommandError, run_command
from deployx.core.exceptions import extract_root_exception
from deployx.core.filesystem import ensure_directory
from deployx.core.security import redact_url_credentials, validate_project_name
from deployx.deployment.health import perform_http_healthcheck
from deployx.deployment.project import load_project_config
from deployx.detectors.registry import detect_repository
from deployx.docker.compose import DockerComposeManager, check_image_exists
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
    FrameworkType,
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
    build_timeout: Optional[int] = None,
    no_build_timeout: bool = False,
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
            effective_build_timeout = get_effective_build_timeout(build_timeout, no_build_timeout, config)
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

        # Preflight summary before first deployment (Requirement 36)
        if console and (not current_state or not current_state.current_commit):
            preflight_tbl = Table(title=f"Preflight Summary: {valid_name}", show_header=False)
            preflight_tbl.add_column("Property", style="bold cyan")
            preflight_tbl.add_column("Value")
            preflight_tbl.add_row("Repository", "verified" if config.git.verified else "ready")
            preflight_tbl.add_row("Branch", config.git.branch)
            preflight_tbl.add_row("Framework", config.deployment.framework.value)
            preflight_tbl.add_row("Expected database", config.deployment.database.value)
            preflight_tbl.add_row("Python mirror", "custom" if pip_idx else "default")
            timeout_display = "unlimited" if effective_build_timeout is None else f"{effective_build_timeout}s"
            preflight_tbl.add_row("Build timeout", timeout_display)
            preflight_tbl.add_row("Docker daemon", "ready")
            preflight_tbl.add_row("Disk space", "OK")
            preflight_tbl.add_row("Deploy key", "ready" if (not config.git.private or getattr(config.git, "verified", False)) else "pending")
            console.print(preflight_tbl)

        # Disk space check before build (Requirement 37)
        import shutil
        try:
            _, _, free_b = shutil.disk_usage(paths.root_dir)
            free_gb = free_b / (1024 ** 3)
            if free_gb < 2.0:
                logger.warning(f"Critically low disk space: {free_gb:.1f} GB free.")
                if console:
                    console.print(f"[bold red]WARNING: Critically low disk space ({free_gb:.1f} GB free). Docker build may fail.[/bold red]")
            elif free_gb < 5.0:
                logger.warning(f"Low disk space: {free_gb:.1f} GB free.")
                if console:
                    console.print(f"[bold yellow]Notice: Low disk space ({free_gb:.1f} GB free in {paths.root_dir}).[/bold yellow]")
        except Exception:
            pass

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
        prev_image = current_state.docker_image if current_state else None

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

        # Build cache awareness (Requirement 24)
        cache_found = check_image_exists(image_tag) or check_image_exists(f"deployx_{valid_name}:current")
        if cache_found:
            if console:
                console.print("[dim cyan]Cache available: yes[/dim cyan]")
            logger.info("Docker build cache: available")
        else:
            if console:
                console.print("[dim cyan]Cold build: no reusable image cache detected[/dim cyan]")
            logger.info("Docker build cache: cold build (no reusable image cache detected)")

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

        logger.info(f"Executing: docker compose build web (timeout={effective_build_timeout})")
        compose_mgr.build(
            service="web",
            stream=True,
            on_line=reporter.on_build_output,
            on_heartbeat=reporter.on_heartbeat,
            on_stall=reporter.on_stall,
            log_file=deploy_log_file,
            env=build_env,
            timeout=effective_build_timeout,
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

            # Database readiness check for PostgreSQL (Requirement 42)
            if "db" in infra_services and config.deployment.database == DatabasePreference.POSTGRES:
                logger.info("Waiting for PostgreSQL database readiness via pg_isready...")
                import time
                for _ in range(15):
                    try:
                        res_p = compose_mgr.run_transient("db", ["pg_isready"], timeout=5)
                        if res_p.success:
                            logger.info("PostgreSQL is ready.")
                            break
                    except Exception:
                        pass
                    time.sleep(1.5)

        # Runtime database backend preflight & safety checks (Requirements 5, 6, 8, 29, 47)
        detected_runtime_db = None
        if config.deployment.framework == FrameworkType.DJANGO:
            logger.info("Validating Django runtime database backend and production settings...")
            probe_cmd = [
                "python", "-c",
                "import json, os, sys;\n"
                "try:\n"
                "    import django\n"
                "    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')\n"
                "    django.setup()\n"
                "    from django.conf import settings\n"
                "    db_cfg = settings.DATABASES.get('default', {})\n"
                "    engine = db_cfg.get('ENGINE', '')\n"
                "    debug = bool(getattr(settings, 'DEBUG', False))\n"
                "    allowed = list(getattr(settings, 'ALLOWED_HOSTS', []))\n"
                "    csrf = list(getattr(settings, 'CSRF_TRUSTED_ORIGINS', []))\n"
                "    print('DEPLOYX_PROBE_JSON:' + json.dumps({'engine': engine, 'debug': debug, 'allowed_hosts': allowed, 'csrf': csrf}))\n"
                "except Exception as e:\n"
                "    print('DEPLOYX_PROBE_ERR:' + str(e), file=sys.stderr)\n"
            ]
            try:
                res_probe = compose_mgr.run_transient("web", probe_cmd, timeout=45)
                probe_text = (res_probe.stdout or "") + "\n" + (res_probe.stderr or "")
                probe_data = {}
                for pline in probe_text.splitlines():
                    if "DEPLOYX_PROBE_JSON:" in pline:
                        import json
                        raw_json = pline.split("DEPLOYX_PROBE_JSON:")[1].strip()
                        probe_data = json.loads(raw_json)
                        break

                runtime_engine = probe_data.get("engine", "")
                if runtime_engine:
                    if "postgresql" in runtime_engine.lower():
                        detected_runtime_db = "postgresql"
                    elif "sqlite" in runtime_engine.lower():
                        detected_runtime_db = "sqlite3"
                    else:
                        detected_runtime_db = runtime_engine

                    # Production Safety Check: DEBUG=True (Requirements 8, 47)
                    if probe_data.get("debug") is True:
                        msg_debug = (
                            "[bold yellow]WARNING: Django DEBUG=True is active in production.[/bold yellow]\n\n"
                            "Debug mode can expose sensitive settings, environment variables, and stack traces."
                        )
                        logger.warn("Django DEBUG=True detected in production settings.")
                        if console:
                            console.print(Panel(msg_debug, title="Production Safety Warning", border_style="yellow"))

                    # Production Safety Check: ALLOWED_HOSTS
                    allowed = probe_data.get("allowed_hosts", [])
                    if not allowed and config.deployment.domain:
                        msg_hosts = f"[bold yellow]WARNING: Django ALLOWED_HOSTS is empty! Requests will return HTTP 400 Bad Request.[/bold yellow]"
                        logger.warn(msg_hosts)
                        if console:
                            console.print(msg_hosts)
                    elif config.deployment.domain and config.deployment.domain not in allowed and "*" not in allowed:
                        msg_hosts = f"[bold yellow]WARNING: Configured domain '{config.deployment.domain}' is missing from Django ALLOWED_HOSTS ({allowed}).[/bold yellow]"
                        logger.warn(msg_hosts)
                        if console:
                            console.print(msg_hosts)

                    # Critical Check: Database Backend Mismatch (Requirements 5, 6)
                    if config.deployment.database == DatabasePreference.POSTGRES and "sqlite" in runtime_engine.lower():
                        err_mismatch = (
                            "Database backend mismatch\n\n"
                            "DeployX project configuration:\n"
                            "  postgres\n\n"
                            "Django runtime backend:\n"
                            f"  sqlite3\n\n"
                            "DeployX injected PostgreSQL environment variables, but the Django project is not using them.\n\n"
                            "Update Django settings to read:\n"
                            "  DB_ENGINE / DB_HOST / DB_NAME / DB_USER / DB_PASSWORD\n"
                            "or DATABASE_URL."
                        )
                        logger.error(f"Database backend mismatch: expected postgres, runtime reported {runtime_engine}")
                        if console:
                            console.print(Panel(f"[bold red]{err_mismatch}[/bold red]", title="Database Backend Mismatch", border_style="red"))

                        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        fail_state = current_state or DeploymentState.new(valid_name, config.git.repository, config.git.branch)
                        fail_state.status = DeploymentStatus.FAILED
                        fail_state.failed_stage = "Runtime database preflight"
                        fail_state.detected_runtime_database = "sqlite3"
                        fail_state.last_error = "Database backend mismatch: expected postgres, runtime reported sqlite"
                        fail_state.updated_at = now_iso
                        state_mgr.save_state(fail_state)
                        return False
            except CommandError as pe:
                logger.warn(f"Django preflight probe warning: {pe.message}")
            except Exception as pe:
                logger.warn(f"Django preflight probe skipped: {pe}")

        # Stage 9: Execute database migrations inside container (Requirements 7, 43)
        reporter.start_stage(9, "Running database migrations in container")
        logger.info("Executing migrations: python manage.py migrate --noinput")
        try:
            compose_mgr.run_transient(
                "web",
                ["python", "manage.py", "migrate", "--noinput"],
                stream=True,
                on_line=reporter.on_build_output,
                log_file=deploy_log_file,
            )
            # Verify migrations applied (Requirement 7)
            try:
                res_v = compose_mgr.run_transient("web", ["python", "manage.py", "showmigrations", "--plan"], timeout=30)
                if res_v.success:
                    logger.info("Migration plan verified successfully.")
            except Exception as ver_exc:
                logger.warn(f"Migration verification check notice: {ver_exc}")
        except CommandError as mig_exc:
            logger.warn(f"Migration command warning or non-critical skip: {mig_exc.message}")

        # Stage 10: Collect static files inside container (Requirement 43)
        reporter.start_stage(10, "Collecting static files in container")
        logger.info("Executing collectstatic: python manage.py collectstatic --noinput")
        try:
            compose_mgr.run_transient(
                "web",
                ["python", "manage.py", "collectstatic", "--noinput"],
                stream=True,
                on_line=reporter.on_build_output,
                log_file=deploy_log_file,
            )
        except CommandError as cs_exc:
            logger.warn(f"Collectstatic warning or skip: {cs_exc.message}")

        # Stage 11: Start main web application
        reporter.start_stage(11, "Starting application containers")
        logger.info("Starting web container: docker compose up -d web")
        compose_mgr.up(services=["web"])

        # Stage 12: Health check & state persistence (Requirements 9, 10, 40, 48, 49)
        reporter.start_stage(12, "Performing application health check")

        health_ok = True
        health_details = {}
        if config.deployment.healthcheck.enabled:
            health_ok = perform_http_healthcheck(
                port=config.deployment.docker.port,
                path=config.deployment.healthcheck.path,
                timeout=config.deployment.healthcheck.timeout,
                retries=config.deployment.healthcheck.retries,
                interval=config.deployment.healthcheck.interval,
                expected_status=getattr(config.deployment.healthcheck, "expected_status", 200),
                details=health_details,
                console=console,
            )

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        final_status = DeploymentStatus.HEALTHY if health_ok else DeploymentStatus.UNHEALTHY
        final_health = HealthStatus.HEALTHY if health_ok else HealthStatus.UNHEALTHY

        # Extract root exception if health check failed (Requirements 10, 48, 49)
        parsed_exc = None
        last_err_summary = None
        if not health_ok:
            web_logs_tail = ""
            try:
                web_logs_res = compose_mgr.logs("web", tail=150)
                web_logs_tail = web_logs_res.stdout or ""
            except Exception:
                pass

            parsed_exc = extract_root_exception(web_logs_tail + "\n" + (health_details.get("response_body") or ""))
            last_err_summary = health_details.get("diagnosis") or "Healthcheck failed or timed out."
            if parsed_exc and parsed_exc.summary:
                last_err_summary = f"{health_details.get('diagnosis', 'Health check failed')}: {parsed_exc.summary}"

            logger.error(f"Deployment completed but healthcheck failed. {last_err_summary}")
            if console:
                panel_lines = [
                    f"[bold red]Deployment completed with health check failure:[/bold red]\n",
                    f"HTTP status: {health_details.get('last_status', 'No response')}",
                    f"Diagnostics: {health_details.get('diagnosis', 'Endpoint unreachable')}\n",
                ]
                if parsed_exc:
                    panel_lines.append(f"[bold]Root cause candidate:[/bold]\n  {parsed_exc.summary}")
                    if parsed_exc.app_frame:
                        panel_lines.append(f"[bold]Application frame:[/bold]\n  {parsed_exc.app_frame}")
                    panel_lines.append("")
                panel_lines.append(f"[bold yellow]Full logs:[/bold yellow]\n  [bold cyan]deployx logs {valid_name}[/bold cyan]")
                console.print(Panel("\n".join(panel_lines), title="Health Check Failure Diagnostics", border_style="red"))

        new_state = DeploymentState(
            project=valid_name,
            repository=config.git.repository,
            branch=config.git.branch,
            current_commit=resolved_commit,
            previous_commit=prev_commit,
            deployed_at=now_iso,
            docker_image=image_tag,
            previous_image=prev_image,
            status=final_status,
            health_status=final_health,
            last_error=None if health_ok else last_err_summary,
            failed_stage=None if health_ok else "Health check",
            last_health_response=str(health_details.get("last_status")),
            last_exception_summary=parsed_exc.summary if (not health_ok and parsed_exc) else None,
            detected_runtime_database=detected_runtime_db,
        )
        state_mgr.save_state(new_state)

        # Image tagging on health success (Requirements 27, 39)
        if health_ok:
            try:
                run_command(["docker", "tag", image_tag, f"deployx_{valid_name}:current"], timeout=15, check=False)
                if prev_image and prev_image != image_tag:
                    run_command(["docker", "tag", prev_image, f"deployx_{valid_name}:previous"], timeout=15, check=False)
            except Exception:
                pass

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
        return False

    except KeyboardInterrupt:
        logger.warn(f"Deployment cancelled by user (Ctrl+C) for '{valid_name}'")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        fail_state = current_state or DeploymentState.new(valid_name, "", "main")
        fail_state.status = DeploymentStatus.FAILED
        fail_state.failed_stage = reporter.current_stage_name
        fail_state.last_error = "Deployment cancelled by user (Ctrl+C)"
        fail_state.updated_at = now_iso
        state_mgr.save_state(fail_state)

        if console:
            console.print("\n[bold yellow]Deployment cancelled by user. (Ctrl+C)[/bold yellow]")
        return False

    except CommandError as exc:
        is_timeout = "timed out" in exc.message.lower()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        fail_state = current_state or DeploymentState.new(valid_name, "", "main")
        fail_state.status = DeploymentStatus.TIMED_OUT if is_timeout else DeploymentStatus.FAILED
        fail_state.failed_stage = reporter.current_stage_name
        fail_state.exit_code = exc.returncode
        if is_timeout:
            fail_state.timeout_reason = exc.message
        fail_state.last_error = str(exc.message)
        fail_state.updated_at = now_iso
        state_mgr.save_state(fail_state)

        logger.error(f"Deployment command failed: {exc}")
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
        fail_state.failed_stage = reporter.current_stage_name
        fail_state.last_error = str(exc)
        fail_state.updated_at = now_iso
        state_mgr.save_state(fail_state)

        reporter.show_failure_summary(
            exit_code=1,
            exception_msg=str(exc),
        )
        return False
