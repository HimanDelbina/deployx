"""
DeployX Progress Reporting and Live Output System
Provides real-time BuildKit parsing, stage tracking, heartbeat monitoring,
stall warnings, verbose streaming, and terminal UI ergonomics for long-running deployments.
"""

from __future__ import annotations

import re
import sys
import time
from typing import List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.progress import BarColumn, Progress


DEPLOYMENT_STAGES = [
    (1, "Preflight checks & load configuration"),
    (2, "Syncing repository"),
    (3, "Resolving commit & tagging"),
    (4, "Analyzing project & framework"),
    (5, "Validating deployment configuration"),
    (6, "Generating deployment configurations"),
    (7, "Building Docker image"),
    (8, "Starting infrastructure services"),
    (9, "Running database migrations"),
    (10, "Collecting static files"),
    (11, "Starting application containers"),
    (12, "Application health check"),
]


class BuildKitParser:
    """
    Parses Docker BuildKit and legacy Docker build lines in real time:
    - Extracts step counter (e.g. [4/7] or Step 4/10)
    - Computes approximate build progress percentage
    - Tracks current operation (e.g. RUN pip install, pulling python:3.12-slim)
    - Detects network-sensitive operations for contextual diagnostics
    """

    # Matches BuildKit lines like "#7 [4/7] RUN pip install..." or "[3/10] COPY..."
    RE_BUILDKIT_STEP = re.compile(r"(?:\[|\bStep\s+)(\d+)/(\d+)(?:\]|\s*:)\s*(.*)", re.IGNORECASE)
    # Matches legacy or plain step numbers e.g. Step 3/8 : WORKDIR /app
    RE_BUILDKIT_INTERNAL = re.compile(r"#\d+\s+(?:\[internal\]\s+)?(load metadata for\s+\S+|pulling\s+\S+|transferring\s+\S+)", re.IGNORECASE)
    RE_RUN_CMD = re.compile(r"^(?:RUN|COPY|ADD|WORKDIR|FROM|ENV|EXPOSE|ENTRYPOINT|CMD)\s+(.*)", re.IGNORECASE)

    def __init__(self):
        self.step_current: Optional[int] = None
        self.step_total: Optional[int] = None
        self.approx_percent: Optional[int] = None
        self.current_operation: Optional[str] = None
        self.last_relevant_lines: List[str] = []
        self.max_history: int = 20

    def parse_line(self, line: str) -> None:
        """Parses a streamed line and updates current build state."""
        clean = line.strip()
        if not clean:
            return

        # Maintain recent output history for error diagnostics
        self.last_relevant_lines.append(clean)
        if len(self.last_relevant_lines) > self.max_history:
            self.last_relevant_lines.pop(0)

        # Check for step counter [x/y]
        match_step = self.RE_BUILDKIT_STEP.search(clean)
        if match_step:
            try:
                curr = int(match_step.group(1))
                tot = int(match_step.group(2))
                self.step_current = curr
                self.step_total = tot
                if tot > 0:
                    self.approx_percent = int((curr / tot) * 100)
                op = match_step.group(3).strip()
                if op:
                    self.current_operation = op
            except (ValueError, ZeroDivisionError):
                pass
            return

        # Check for internal image metadata loading or pulling
        match_internal = self.RE_BUILDKIT_INTERNAL.search(clean)
        if match_internal:
            self.current_operation = match_internal.group(1).strip()
            return

        # Check for image pulling lines
        if "pulling from" in clean.lower() or "pull complete" in clean.lower() or "extracting" in clean.lower():
            if not self.current_operation or "pull" not in self.current_operation.lower():
                self.current_operation = clean[:60]
            return

        # Check for RUN or command operations if starting with Docker instruction
        match_cmd = self.RE_RUN_CMD.match(clean)
        if match_cmd:
            self.current_operation = clean[:80]

    def get_progress_label(self) -> str:
        """Returns structured progress label or 'active' if steps unknown."""
        if self.step_current is not None and self.step_total is not None:
            pct = self.approx_percent if self.approx_percent is not None else int((self.step_current / self.step_total) * 100)
            return f"{self.step_current}/{self.step_total} steps (~{pct}%)"
        return "active"

    def is_network_operation(self) -> bool:
        """Returns True if the current operation relies on external network resources."""
        if not self.current_operation:
            return False
        op = self.current_operation.lower()
        network_keywords = (
            "pull", "load metadata", "from ", "pip install", "pip3 install",
            "apt-get", "apt install", "npm install", "yarn install", "pnpm install",
            "curl", "wget", "git clone", "poetry install"
        )
        return any(kw in op for kw in network_keywords)


def format_duration(seconds: float) -> str:
    """Formats seconds into HH:MM:SS format."""
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def make_ascii_progress_bar(percentage: int, width: int = 20) -> str:
    """Renders a clean ASCII/Unicode progress bar: [███████████░░░░░░░░]."""
    pct = max(0, min(100, percentage))
    filled = int(round((pct / 100) * width))
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


class DeploymentProgressReporter:
    """
    Manages end-to-end progress reporting for DeployX deployments.
    Supports interactive Rich Live UI, plain mode (CI), and verbose mode.
    Emits heartbeats and stall warnings automatically.
    """

    def __init__(
        self,
        project_name: str,
        total_stages: int = 12,
        verbose: bool = False,
        plain: bool = False,
        console: Optional[Console] = None,
    ):
        self.project_name = project_name
        self.total_stages = total_stages
        self.verbose = verbose
        self.console = console or Console()
        
        # Plain mode if requested, or if stdout is not an interactive terminal
        is_tty = getattr(self.console, "is_terminal", False) or (sys.stdout and sys.stdout.isatty())
        self.plain = plain or (not is_tty and not verbose)

        self.current_stage: int = 1
        self.current_stage_name: str = "Preflight checks & load configuration"
        self.stage_start_time: float = time.time()
        self.deployment_start_time: float = time.time()
        self.last_activity_time: float = time.time()

        self.parser = BuildKitParser()
        self.live_context = None
        self._last_heartbeat_time: float = self.deployment_start_time
        self.stall_reported: bool = False

    @property
    def overall_percentage(self) -> int:
        """Derives overall deployment progress percentage from completed stages."""
        pct = int((self.current_stage / self.total_stages) * 100)
        return min(100, max(0, pct))

    def start_stage(self, stage_num: int, stage_name: str) -> None:
        """Begins a new deployment stage and prints/updates progress."""
        self.current_stage = stage_num
        self.current_stage_name = stage_name
        self.stage_start_time = time.time()
        self.last_activity_time = self.stage_start_time
        self.stall_reported = False

        pct = self.overall_percentage
        bar = make_ascii_progress_bar(pct, width=20)
        elapsed = format_duration(time.time() - self.deployment_start_time)

        if self.plain or self.verbose:
            self.console.print(
                f"\n[cyan][{stage_num}/{self.total_stages}][/cyan] [bold]{stage_name}...[/bold] "
                f"[dim](Overall: {pct}% {bar} | Elapsed: {elapsed})[/dim]"
            )
        else:
            # Interactive banner
            self.console.print(
                f"[{stage_num}/{self.total_stages}] [cyan]{stage_name}...[/cyan] "
                f"[dim](Overall: {pct}% {bar})[/dim]"
            )

    def on_build_output(self, line: str) -> None:
        """Callback invoked for each streamed line of Docker build output."""
        self.last_activity_time = time.time()
        self.parser.parse_line(line)

        if self.verbose:
            # In verbose mode, show underlying build lines directly
            self.console.print(f"[dim]{line}[/dim]")
        elif self.plain:
            # In plain mode, display step advances without flooding
            if self.parser.step_current is not None and line.strip().startswith(("[", "#")):
                # Check if it's a primary step definition line
                if self.parser.RE_BUILDKIT_STEP.search(line):
                    op = self.parser.current_operation or ""
                    pct = self.parser.approx_percent
                    pct_str = f"~{pct}%" if pct is not None else "active"
                    self.console.print(
                        f"  [Build {pct_str}] Step {self.parser.step_current}/{self.parser.step_total}: {op[:60]}"
                    )

    def on_heartbeat(self, elapsed: float, idle: float) -> None:
        """Emitted when a subprocess is silent for 20-30 seconds."""
        elapsed_str = format_duration(elapsed)
        idle_sec = int(idle)
        self.console.print(
            f"[dim yellow]Still working... Current stage: {self.current_stage_name} | "
            f"Elapsed: {elapsed_str} | Last output: {idle_sec}s ago[/dim yellow]"
        )

    def on_stall(self, elapsed: float, idle: float) -> None:
        """Emitted when no output has been seen for the stall threshold (e.g. 120s)."""
        idle_min = int(idle // 60)
        self.stall_reported = True

        stall_msg = (
            f"[bold yellow]WARNING: No new Docker build output for {idle_min} minutes.[/bold yellow]\n\n"
            f"The process is still running.\n\n"
            f"[bold]Possible causes:[/bold]\n"
            f"  - Slow Docker registry response or large base image pull\n"
            f"  - Package download delay (PyPI / apt / npm mirrors)\n"
            f"  - DNS or network connectivity latency\n"
            f"  - Heavy compilation or C-extension building\n\n"
        )

        if self.parser.is_network_operation() and self.parser.current_operation:
            stall_msg += (
                f"[bold cyan]Contextual Notice:[/bold cyan]\n"
                f"  No build output for {int(idle)}s while running: [dim]{self.parser.current_operation}[/dim]\n"
                f"  Network or package index connectivity may be slow or temporarily constrained.\n\n"
            )

        stall_msg += (
            f"[bold]To troubleshoot:[/bold]\n"
            f"  Inspect live logs: [bold cyan]deployx logs {self.project_name}[/bold cyan]\n"
            f"  Or rerun with:     [bold cyan]deployx deploy {self.project_name} --verbose[/bold cyan]"
        )

        self.console.print(Panel(stall_msg, title="Deployment Activity Notice", border_style="yellow"))

    def show_failure_summary(
        self,
        exit_code: int = 1,
        exception_msg: Optional[str] = None,
    ) -> None:
        """Renders structured failure panel indicating exact stage, last operation, and diagnostics."""
        last_op = self.parser.current_operation or "Unknown / Initialization"
        last_lines = "\n".join(self.parser.last_relevant_lines[-6:]) if self.parser.last_relevant_lines else (exception_msg or "No output recorded.")

        fail_text = (
            f"[bold red]Deployment failed at:[/bold red]\n"
            f"  Step {self.current_stage}/{self.total_stages} - {self.current_stage_name}\n\n"
            f"[bold]Last build operation:[/bold]\n"
            f"  {last_op}\n\n"
            f"[bold]Exit code:[/bold]\n"
            f"  {exit_code}\n\n"
            f"[bold]Last relevant output:[/bold]\n"
            f"[dim]{last_lines}[/dim]\n\n"
            f"[bold yellow]View full log:[/bold yellow]\n"
            f"  [bold cyan]deployx logs {self.project_name}[/bold cyan]\n"
            f"  [dim]or check /opt/deployx/logs/{self.project_name}/deploy.log[/dim]"
        )

        self.console.print(Panel(fail_text, title=f"Deployment Failed: {self.project_name}", border_style="red"))
