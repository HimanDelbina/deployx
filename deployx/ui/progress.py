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
        self.last_event_time: float = time.time()

    def _process_text(self, text: str) -> None:
        """Extracts build step counter and current operation from a line of text."""
        clean = text.strip()
        if not clean:
            return

        # Check for step counter [x/y] e.g. [5/8] RUN pip install...
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

    def parse_line(self, line: str) -> None:
        """Parses a streamed line (plain text or BuildKit/Buildx rawjson) and updates build state."""
        clean = line.strip()
        if not clean:
            return

        # Handle BuildKit / Docker Buildx rawjson event objects
        if clean.startswith("{") and clean.endswith("}"):
            try:
                import base64
                import json
                data = json.loads(clean)
                if isinstance(data, dict):
                    # 1. Check vertex
                    v = data.get("vertex")
                    if isinstance(v, dict):
                        name = v.get("name")
                        if name:
                            self.last_relevant_lines.append(str(name))
                            self._process_text(str(name))
                    elif isinstance(v, str):
                        self.last_relevant_lines.append(v)
                        self._process_text(v)

                    # 2. Check vertices list
                    vertices = data.get("vertices")
                    if isinstance(vertices, list):
                        for item in vertices:
                            if isinstance(item, dict) and item.get("name"):
                                self.last_relevant_lines.append(str(item["name"]))
                                self._process_text(str(item["name"]))

                    # 3. Check statuses list
                    statuses = data.get("statuses")
                    if isinstance(statuses, list):
                        for s in statuses:
                            if isinstance(s, dict):
                                if s.get("name"):
                                    self._process_text(str(s["name"]))
                                if s.get("id"):
                                    self._process_text(str(s["id"]))
                                if "current" in s and "total" in s:
                                    try:
                                        self.step_current = int(s["current"])
                                        self.step_total = int(s["total"])
                                        if self.step_total > 0:
                                            self.approx_percent = int((self.step_current / self.step_total) * 100)
                                    except Exception:
                                        pass

                    # 4. Check logs list
                    logs = data.get("logs")
                    if isinstance(logs, list):
                        for log_entry in logs:
                            if isinstance(log_entry, dict):
                                raw_bytes = log_entry.get("data")
                                if raw_bytes:
                                    try:
                                        decoded = base64.b64decode(raw_bytes).decode("utf-8", errors="replace")
                                        for decoded_line in decoded.splitlines():
                                            self.last_relevant_lines.append(decoded_line.strip())
                                            self._process_text(decoded_line)
                                    except Exception:
                                        pass

                    # 5. Direct keys
                    for key in ("name", "status", "id", "action", "stream", "message"):
                        val = data.get(key)
                        if isinstance(val, str) and val:
                            self.last_relevant_lines.append(val)
                            self._process_text(val)

                    if "current" in data and "total" in data:
                        try:
                            self.step_current = int(data["current"])
                            self.step_total = int(data["total"])
                            if self.step_total > 0:
                                self.approx_percent = int((self.step_current / self.step_total) * 100)
                        except Exception:
                            pass

                    # Bound history length
                    if len(self.last_relevant_lines) > self.max_history:
                        self.last_relevant_lines = self.last_relevant_lines[-self.max_history:]
                    return
            except Exception:
                pass

        # Maintain recent output history for error diagnostics
        self.last_relevant_lines.append(clean)
        if len(self.last_relevant_lines) > self.max_history:
            self.last_relevant_lines.pop(0)

        self._process_text(clean)

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
        config: Optional[Any] = None,
        python_index: Optional[str] = None,
    ):
        self.project_name = project_name
        self.total_stages = total_stages
        self.verbose = verbose
        self.console = console or Console()
        self.config = config
        self.python_index = python_index
        
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
        self._last_stall_panel_time: float = 0.0
        self.stall_threshold: float = 120.0
        self._last_reported_step_op: Optional[tuple[Optional[int], Optional[str]]] = None

    def get_build_status(self) -> str:
        """Returns the current build responsiveness state: ACTIVE, SLOW, or STALLED."""
        idle = time.time() - getattr(self, "last_activity_time", time.time())
        if idle < 45.0:
            return "ACTIVE"
        if idle < self.stall_threshold:
            return "SLOW"
        return "STALLED"

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

        # Track and display build step advances
        curr_step = self.parser.step_current
        tot_step = self.parser.step_total
        op = self.parser.current_operation or ""

        current_pair = (curr_step, op)
        if current_pair != self._last_reported_step_op and (curr_step is not None or op):
            self._last_reported_step_op = current_pair
            if self.plain:
                if curr_step is not None and tot_step is not None:
                    pct = self.parser.approx_percent
                    pct_str = f"~{pct}%" if pct is not None else "active"
                    if op:
                        self.console.print(f"  [Build {pct_str}] Step {curr_step}/{tot_step}: {op[:60]}")
                    else:
                        self.console.print(f"  [Build {pct_str}] Step {curr_step}/{tot_step}")
                elif op:
                    self.console.print(f"  [Build] {op[:60]}")
            elif not self.verbose:
                if curr_step is not None and tot_step is not None:
                    step_prefix = f"Build step {curr_step}/{tot_step}"
                elif curr_step is not None:
                    step_prefix = f"Build step {curr_step}"
                else:
                    step_prefix = "Build step"

                if op:
                    self.console.print(f"  [cyan]{step_prefix}:[/cyan] [bold]{op}[/bold]")
                else:
                    self.console.print(f"  [cyan]{step_prefix}[/cyan]")

        if self.verbose:
            # In verbose mode, show underlying build lines directly
            self.console.print(f"[dim]{line}[/dim]")

    def on_heartbeat(self, elapsed: float, idle: float) -> None:
        """Emitted when a subprocess is silent for 20-30 seconds."""
        elapsed_str = format_duration(elapsed)
        idle_sec = int(idle)
        status = "SLOW" if idle_sec >= 45 else "ACTIVE"
        step_info = f"Step {self.parser.step_current}/{self.parser.step_total} " if (self.parser.step_current and self.parser.step_total) else ""
        op = self.parser.current_operation or self.current_stage_name
        self.console.print(
            f"[dim yellow]Build status: {status} | Current step: {step_info}{op[:50]} | "
            f"Last progress event: {idle_sec}s ago | Elapsed: {elapsed_str}[/dim yellow]"
        )

    def on_stall(self, elapsed: float, idle: float) -> None:
        """Emitted when no output has been seen for the stall threshold (e.g. 120s)."""
        import os
        from deployx.core.security import redact_url_credentials

        now = time.time()
        # Warn once at threshold, repeat at most every 5 minutes (300s)
        if getattr(self, "_last_stall_panel_time", 0.0) > 0 and (now - self._last_stall_panel_time) < 300.0:
            return

        self.stall_reported = True
        self._last_stall_panel_time = now

        idle_min = int(idle // 60)
        idle_sec = int(idle)

        # Resolve configured Python index
        index_label = "Default (PyPI)"
        if self.python_index:
            index_label = f"Custom ({redact_url_credentials(self.python_index)})"
        elif self.config:
            py_build = getattr(getattr(self.config, "build", None), "python", None)
            eff = os.environ.get("PIP_INDEX_URL") or (py_build.index_url if py_build else None)
            if eff:
                index_label = f"Custom ({redact_url_credentials(eff)})"
        elif os.environ.get("PIP_INDEX_URL"):
            index_label = f"Custom ({redact_url_credentials(os.environ['PIP_INDEX_URL'])})"

        current_step = self.parser.current_operation or "Unknown / Initialization"

        stall_msg = (
            f"[bold yellow]WARNING: No new Docker build output for {idle_min} minutes.[/bold yellow]\n"
            f"No new output for {idle_sec} seconds.\n\n"
            f"[bold yellow]Build status: STALLED[/bold yellow]\n"
            f"[bold]Current build step:[/bold]\n"
            f"{current_step}\n\n"
            f"[bold]Current step:[/bold] {current_step}\n"
            f"[bold]Last progress event:[/bold] {idle_sec}s ago (threshold: {idle_min}m)\n\n"
            f"Possible network/package index issue.\n"
            f"Network or package index connectivity may be slow or temporarily constrained.\n\n"
            f"[bold]Configured Python index:[/bold]\n"
            f"{index_label}\n\n"
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
