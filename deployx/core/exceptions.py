"""
DeployX Exception Diagnostics and Safe Error Parser
Extracts structured root-cause candidates from Django HTML debug pages and container logs.
Redacts credentials and protects against secret leakage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from deployx.core.security import redact_sensitive_text


@dataclass
class ParsedDjangoException:
    exception_type: Optional[str] = None
    exception_value: Optional[str] = None
    app_frame: Optional[str] = None
    summary: Optional[str] = None


RE_HTML_TITLE = re.compile(r"<title>\s*([A-Za-z0-9_.]*(?:Error|Exception|DoesNotExist|MultipleObjectsReturned))\s*(?:at\s+[^<]+)?</title>", re.IGNORECASE)
RE_HTML_H1 = re.compile(r"<h1>\s*([A-Za-z0-9_.]*(?:Error|Exception|DoesNotExist|MultipleObjectsReturned))\s*</h1>", re.IGNORECASE)
RE_HTML_EXC_TYPE = re.compile(r"<th>\s*Exception Type:\s*</th>\s*<td>\s*([^<]+)\s*</td>", re.IGNORECASE)
RE_HTML_EXC_VAL = re.compile(r"(?:<th>\s*Exception Value:\s*</th>\s*<td>\s*(?:<pre>)?|<pre class=['\"]exception_value['\"]>)\s*([^<]+)\s*(?:</pre>)?", re.IGNORECASE)
RE_HTML_FRAME = re.compile(r'<div class="commands">\s*<a href="[^"]*">([^<]+)</a>', re.IGNORECASE)

RE_TRACEBACK_START = re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE)
RE_FRAME_LINE = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\w+))?')
RE_EXCEPTION_LINE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_.]*(?:Error|Exception|DoesNotExist|MultipleObjectsReturned|OperationalError|ProgrammingError|IntegrityError|Configured|Failed|Warning|[A-Z][a-zA-Z0-9_]+)):\s*(.*)$")


def parse_django_exception_from_html(html_text: str) -> Optional[ParsedDjangoException]:
    """
    Safely parses a Django technical 500 error page.
    Never exposes raw HTML, request META, or server settings.
    """
    if not html_text:
        return None

    exc_type = None
    exc_value = None
    app_frame = None

    match_type = RE_HTML_EXC_TYPE.search(html_text)
    if match_type:
        exc_type = match_type.group(1).strip()

    match_val = RE_HTML_EXC_VAL.search(html_text)
    if match_val:
        exc_value = match_val.group(1).strip()

    if not exc_type:
        match_title = RE_HTML_TITLE.search(html_text)
        if match_title:
            exc_type = match_title.group(1).strip()

    if not exc_type:
        match_h1 = RE_HTML_H1.search(html_text)
        if match_h1:
            exc_type = match_h1.group(1).strip()

    match_frame = RE_HTML_FRAME.search(html_text)
    if match_frame:
        app_frame = match_frame.group(1).strip()

    if not exc_type and not exc_value:
        return None

    # Redact any sensitive information
    if exc_value:
        exc_value = redact_sensitive_text(exc_value)

    summary = f"{exc_type}: {exc_value}" if (exc_type and exc_value) else (exc_type or exc_value)
    return ParsedDjangoException(
        exception_type=exc_type,
        exception_value=exc_value,
        app_frame=app_frame,
        summary=summary,
    )


def parse_django_exception_from_logs(log_text: str) -> Optional[ParsedDjangoException]:
    """
    Safely extracts the root exception and top application frame from container logs / traceback text.
    """
    if not log_text:
        return None

    lines = log_text.splitlines()
    frames = []
    exc_type = None
    exc_value = None

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        m_frame = RE_FRAME_LINE.search(line)
        if m_frame:
            file_path = m_frame.group(1)
            line_num = m_frame.group(2)
            fn = m_frame.group(3) or ""
            frames.append((file_path, line_num, fn))

        m_exc = RE_EXCEPTION_LINE.match(line)
        if m_exc:
            exc_type = m_exc.group(1).split(".")[-1]
            exc_value = m_exc.group(2).strip()

    if not exc_type and not exc_value:
        # Fallback: check for standard Python exceptions on last lines
        for line in reversed(lines[-20:]):
            clean = line.strip()
            if any(clean.startswith(err) for err in ("OperationalError:", "ProgrammingError:", "django.db", "ImportError:", "ModuleNotFoundError:")):
                parts = clean.split(":", 1)
                exc_type = parts[0].split(".")[-1].strip()
                exc_value = parts[1].strip() if len(parts) > 1 else ""
                break

    if not exc_type and not exc_value:
        return None

    # Choose top application frame (prefer non-site-packages)
    app_frame_str = None
    app_frames = [
        f"{f[0]}:{f[1]}"
        for f in frames
        if "site-packages" not in f[0] and "/lib/python" not in f[0] and "\\lib\\python" not in f[0]
    ]
    if app_frames:
        # Last app frame in traceback is closest to origin
        app_frame_str = app_frames[-1]
    elif frames:
        last_f = frames[-1]
        app_frame_str = f"{last_f[0]}:{last_f[1]}"

    if exc_value:
        exc_value = redact_sensitive_text(exc_value)

    summary = f"{exc_type}: {exc_value}" if (exc_type and exc_value) else (exc_type or exc_value)
    return ParsedDjangoException(
        exception_type=exc_type,
        exception_value=exc_value,
        app_frame=app_frame_str,
        summary=summary,
    )


def extract_root_exception(content: str) -> Optional[ParsedDjangoException]:
    """
    Heuristically checks if content is HTML debug or log text and extracts root exception safely.
    """
    if "<html" in content.lower() or "<!doctype" in content.lower() or "<th>Exception" in content:
        res = parse_django_exception_from_html(content)
        if res:
            return res
    return parse_django_exception_from_logs(content)


def diagnose_health_error(status_code: Optional[int], error_str: Optional[str] = None) -> str:
    """
    Classifies HTTP status codes and network errors into actionable diagnostics.
    """
    if status_code == 400:
        return "HTTP 400 Bad Request: Likely invalid or missing ALLOWED_HOSTS setting in Django, or Host header mismatch."
    elif status_code == 403:
        return "HTTP 403 Forbidden: Potential CSRF verification failure, authentication requirement, or security middleware block."
    elif status_code == 404:
        return "HTTP 404 Not Found: Health check path mismatch. Verify configured path in deployx.yml matches application URLconf."
    elif status_code == 500:
        return "HTTP 500 Internal Server Error: Application runtime or database error occurred during request handling."
    elif status_code and status_code >= 502:
        return f"HTTP {status_code}: Gateway or upstream server error. Application process may not be responding."

    err_lower = (error_str or "").lower()
    if "connection refused" in err_lower or "connectionrefused" in err_lower:
        return "Connection refused: Application process is not listening on the expected port."
    if "connection reset" in err_lower or "connectionreset" in err_lower or "connection closed" in err_lower:
        return "Connection reset: Application process may be restarting or crashing immediately on connect."
    if "timed out" in err_lower or "timeout" in err_lower:
        return "Connection timed out: Application is unresponsive or blocked on an external call/database lock."

    return error_str or "Health check did not receive a successful response."


classify_http_failure = diagnose_health_error
