"""
DeployX Secure Subprocess Command Runner
Executes system commands with strict argument arrays (no shell=True),
enforces timeouts, redacts secrets, and returns structured actionable errors.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from deployx.core.security import redact_sensitive_text


@dataclass
class CommandResult:
    """Encapsulates the result of an executed system command."""
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float

    @property
    def success(self) -> bool:
        return self.returncode == 0


class CommandError(Exception):
    """Exception raised when a system command fails."""
    def __init__(
        self,
        message: str,
        command: list[str],
        returncode: int,
        stdout: str = "",
        stderr: str = "",
        actionable_advice: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.actionable_advice = actionable_advice

    def __str__(self) -> str:
        res = [f"{self.message} (exit code: {self.returncode})"]
        if self.actionable_advice:
            res.append(f"Suggestion: {self.actionable_advice}")
        if self.stderr.strip():
            res.append(f"Stderr: {self.stderr.strip()}")
        return "\n".join(res)


def diagnose_command_failure(cmd: list[str], returncode: int, stderr: str, stdout: str) -> str | None:
    """
    Translates common Linux/Git/Docker errors into actionable advice.
    """
    combined = (stderr + "\n" + stdout).lower()
    first_arg = cmd[0].lower() if cmd else ""

    # Git authentication & network failures
    if "git" in first_arg:
        if "permission denied (publickey)" in combined:
            return (
                "Git authentication failed. Verify that the Deploy Key has been added to the "
                "GitHub repository (Settings -> Deploy keys) and has read access."
            )
        if "repository not found" in combined:
            return (
                "Git repository not found. Verify the repository URL and check that your Deploy Key "
                "or account has access to this repository."
            )
        if "host key verification failed" in combined:
            return (
                "SSH host key verification failed for GitHub. Ensure github.com is in known_hosts "
                "or run: ssh-keyscan github.com >> ~/.ssh/known_hosts"
            )
        if "could not resolve host" in combined:
            return "DNS resolution failed. Check your server's internet connection and /etc/resolv.conf."

    # Docker failures
    if "docker" in first_arg:
        if (
            "is the docker daemon running" in combined
            or "cannot connect to the docker daemon" in combined
        ):
            return "Docker daemon is not running. Start Docker with: sudo systemctl start docker"
        if "permission denied while connecting to the docker daemon" in combined:
            return (
                "Permission denied accessing Docker daemon. Ensure current user is in the 'docker' group "
                "or run DeployX with appropriate permissions."
            )
        if "bind: address already in use" in combined or "port is already allocated" in combined:
            return "Required network port is already allocated. Inspect listening ports with: sudo ss -tulpn"
        if "no space left on device" in combined:
            return "Docker disk space exhausted. Clean unused images and containers with: docker system prune -af"

    return None


def run_command(
    cmd: list[str],
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    check: bool = True,
    sensitive_strings: list[str] | None = None,
) -> CommandResult:
    """
    Executes a system command securely:
    - Strictly shell=False.
    - Requires cmd as a list of strings.
    - Applies execution timeout.
    - Redacts sensitive tokens from output and exceptions.
    - Translates known failure patterns into actionable guidance.
    """
    if not isinstance(cmd, (list, tuple)) or not cmd:
        raise ValueError("Command must be a non-empty list of string arguments.")

    str_cmd = [str(arg) for arg in cmd]
    
    # Merge custom environment with os.environ safely
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)

    start_time = time.time()
    try:
        process = subprocess.run(
            str_cmd,
            cwd=str(cwd) if cwd else None,
            env=cmd_env,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        duration = time.time() - start_time
    except subprocess.TimeoutExpired as exc:
        duration = time.time() - start_time
        safe_cmd = redact_sensitive_text(" ".join(str_cmd), sensitive_strings)
        raise CommandError(
            message=f"Command timed out after {timeout} seconds: {safe_cmd}",
            command=str_cmd,
            returncode=-1,
            stdout=redact_sensitive_text(exc.stdout or "", sensitive_strings),
            stderr=redact_sensitive_text(exc.stderr or "", sensitive_strings),
            actionable_advice=f"The operation took longer than {timeout}s. Consider increasing timeout if building large images.",
        ) from exc
    except FileNotFoundError as exc:
        duration = time.time() - start_time
        executable = str_cmd[0]
        raise CommandError(
            message=f"Required executable '{executable}' not found on system PATH.",
            command=str_cmd,
            returncode=127,
            actionable_advice=f"Install '{executable}' using apt or your system package manager.",
        ) from exc
    except Exception as exc:
        duration = time.time() - start_time
        safe_cmd = redact_sensitive_text(" ".join(str_cmd), sensitive_strings)
        raise CommandError(
            message=f"Execution error running '{safe_cmd}': {exc}",
            command=str_cmd,
            returncode=-1,
        ) from exc

    raw_stdout = process.stdout or ""
    raw_stderr = process.stderr or ""
    clean_stdout = redact_sensitive_text(raw_stdout, sensitive_strings)
    clean_stderr = redact_sensitive_text(raw_stderr, sensitive_strings)

    result = CommandResult(
        command=str_cmd,
        returncode=process.returncode,
        stdout=clean_stdout,
        stderr=clean_stderr,
        duration=duration,
    )

    if check and not result.success:
        safe_cmd = redact_sensitive_text(" ".join(str_cmd), sensitive_strings)
        advice = diagnose_command_failure(str_cmd, process.returncode, clean_stderr, clean_stdout)
        raise CommandError(
            message=f"Command '{safe_cmd}' failed with exit code {process.returncode}",
            command=str_cmd,
            returncode=process.returncode,
            stdout=clean_stdout,
            stderr=clean_stderr,
            actionable_advice=advice,
        )

    return result


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Safely terminates a child process and its descendants."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    except Exception:
        pass


def run_command_streaming(
    cmd: list[str],
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    check: bool = True,
    sensitive_strings: list[str] | None = None,
    on_line: Callable[[str], None] | None = None,
    on_stdout_line: Callable[[str], None] | None = None,
    on_stderr_line: Callable[[str], None] | None = None,
    on_heartbeat: Callable[[float, float], None] | None = None,
    heartbeat_interval: float = 25.0,
    stall_timeout: float | None = 120.0,
    on_stall: Callable[[float, float], None] | None = None,
    log_file: Path | str | None = None,
) -> CommandResult:
    """
    Executes a system command securely with real-time output streaming:
    - Strictly shell=False with argument array.
    - Real-time line-by-line streaming of stdout and stderr via non-blocking worker threads.
    - Live secret redaction applied to each streamed line and collected buffers.
    - Clean Ctrl+C handling to prevent orphaned processes.
    - Periodic activity heartbeats for silent processes.
    - Configurable stall warning detection without premature termination.
    - Optional synchronous logging to disk as lines arrive.
    """
    import queue
    import threading
    from typing import Callable

    if not isinstance(cmd, (list, tuple)) or not cmd:
        raise ValueError("Command must be a non-empty list of string arguments.")

    str_cmd = [str(arg) for arg in cmd]

    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)

    start_time = time.time()
    try:
        proc = subprocess.Popen(
            str_cmd,
            cwd=str(cwd) if cwd else None,
            env=cmd_env,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        duration = time.time() - start_time
        executable = str_cmd[0]
        raise CommandError(
            message=f"Required executable '{executable}' not found on system PATH.",
            command=str_cmd,
            returncode=127,
            actionable_advice=f"Install '{executable}' using apt or your system package manager.",
        ) from exc
    except Exception as exc:
        duration = time.time() - start_time
        safe_cmd = redact_sensitive_text(" ".join(str_cmd), sensitive_strings)
        raise CommandError(
            message=f"Execution error running '{safe_cmd}': {exc}",
            command=str_cmd,
            returncode=-1,
        ) from exc

    output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def _reader(stream, stream_name: str):
        try:
            for raw_line in iter(stream.readline, ""):
                output_queue.put((stream_name, raw_line))
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass
            output_queue.put((stream_name, None))

    t_stdout = threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True)
    t_stderr = threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True)
    t_stdout.start()
    t_stderr.start()

    active_streams = 2
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    last_output_time = time.time()
    last_heartbeat_time = last_output_time
    last_stall_warning_time = 0.0
    stall_signaled = False

    log_fp = None
    if log_file:
        try:
            p_log = Path(log_file)
            p_log.parent.mkdir(parents=True, exist_ok=True)
            log_fp = open(p_log, "a", encoding="utf-8")
        except Exception:
            log_fp = None

    try:
        while active_streams > 0 or not output_queue.empty():
            try:
                stream_name, raw_line = output_queue.get(timeout=0.25)
            except queue.Empty:
                now = time.time()
                elapsed = now - start_time
                idle = now - last_output_time

                # Check process completion if no active streams
                if proc.poll() is not None and active_streams == 0:
                    break

                # Overall timeout check
                if timeout and elapsed > timeout:
                    _kill_process_tree(proc)
                    safe_cmd = redact_sensitive_text(" ".join(str_cmd), sensitive_strings)
                    raise CommandError(
                        message=f"Command timed out after {timeout} seconds: {safe_cmd}",
                        command=str_cmd,
                        returncode=-1,
                        stdout="\n".join(stdout_lines),
                        stderr="\n".join(stderr_lines),
                        actionable_advice=f"The operation took longer than {timeout}s.",
                    )

                # Stall detection check (warn once, repeat only at ~5 min intervals)
                if stall_timeout and idle >= stall_timeout:
                    if not stall_signaled or (now - last_stall_warning_time) >= 300.0:
                        if on_stall:
                            on_stall(elapsed, idle)
                        stall_signaled = True
                        last_stall_warning_time = now

                # Periodic heartbeat check
                elif (
                    on_heartbeat
                    and heartbeat_interval
                    and idle >= heartbeat_interval
                    and (now - last_heartbeat_time) >= heartbeat_interval
                ):
                    on_heartbeat(elapsed, idle)
                    last_heartbeat_time = now

                continue

            if raw_line is None:
                active_streams -= 1
                continue

            now = time.time()
            clean_line = redact_sensitive_text(raw_line.rstrip("\r\n"), sensitive_strings)
            last_output_time = now
            last_heartbeat_time = now
            stall_signaled = False

            if stream_name == "stdout":
                stdout_lines.append(clean_line)
                if on_stdout_line:
                    on_stdout_line(clean_line)
            else:
                stderr_lines.append(clean_line)
                if on_stderr_line:
                    on_stderr_line(clean_line)

            if on_line:
                on_line(clean_line)

            if log_fp:
                try:
                    log_fp.write(clean_line + "\n")
                    log_fp.flush()
                except Exception:
                    pass

    except KeyboardInterrupt:
        _kill_process_tree(proc)
        if log_fp:
            try:
                log_fp.write("\n[DeployX] Command cancelled by user (SIGINT).\n")
                log_fp.flush()
                log_fp.close()
            except Exception:
                pass
        raise
    finally:
        if log_fp:
            try:
                log_fp.close()
            except Exception:
                pass

    returncode = proc.wait()
    duration = time.time() - start_time
    clean_stdout = "\n".join(stdout_lines)
    clean_stderr = "\n".join(stderr_lines)

    result = CommandResult(
        command=str_cmd,
        returncode=returncode,
        stdout=clean_stdout,
        stderr=clean_stderr,
        duration=duration,
    )

    if check and not result.success:
        safe_cmd = redact_sensitive_text(" ".join(str_cmd), sensitive_strings)
        advice = diagnose_command_failure(str_cmd, returncode, clean_stderr, clean_stdout)
        raise CommandError(
            message=f"Command '{safe_cmd}' failed with exit code {returncode}",
            command=str_cmd,
            returncode=returncode,
            stdout=clean_stdout,
            stderr=clean_stderr,
            actionable_advice=advice,
        )

    return result
