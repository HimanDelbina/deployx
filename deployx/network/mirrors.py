"""
DeployX Zero-Touch PyPI Connectivity, Mirror Selection & Failover Engine
Automatically probes package mirrors, selects the most responsive available mirror,
persists global configuration, and handles dynamic failover during Docker builds.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from deployx.config import load_global_config, paths
from deployx.core.filesystem import atomic_write_file
from deployx.core.security import redact_url_credentials

DEFAULT_MIRROR_CANDIDATES: List[str] = [
    "https://pypi.org/simple/",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple/",
    "https://mirror.sjtu.edu.cn/pypi/web/simple/",
]


def test_mirror(url: str, timeout: float = 3.5) -> Tuple[bool, float, str]:
    """
    Probes reachability and latency of a Python package mirror.
    Returns (is_healthy, latency_seconds, error_or_status).
    """
    clean_url = url.rstrip("/") + "/"
    probe_url = clean_url + "pip/"
    start_time = time.monotonic()
    try:
        req = urllib.request.Request(
            probe_url,
            headers={"User-Agent": "DeployX-MirrorProbe/0.2"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency = time.monotonic() - start_time
            code = getattr(resp, "status", None) or (resp.getcode() if hasattr(resp, "getcode") else 200)
            if 200 <= code < 400:
                return (True, latency, f"HTTP {code}")
            return (False, latency, f"HTTP {code}")
    except urllib.error.HTTPError as exc:
        latency = time.monotonic() - start_time
        # Some mirrors return 404 for /pip/ but 200 for index root
        if exc.code in (404, 301, 302, 403):
            try:
                req_root = urllib.request.Request(clean_url, headers={"User-Agent": "DeployX-MirrorProbe/0.2"})
                with urllib.request.urlopen(req_root, timeout=timeout) as resp_root:
                    root_code = getattr(resp_root, "status", None) or (resp_root.getcode() if hasattr(resp_root, "getcode") else 200)
                    if 200 <= root_code < 400:
                        return (True, time.monotonic() - start_time, f"HTTP {root_code}")
            except Exception:
                pass
        return (False, latency, f"HTTP {exc.code}")
    except Exception as exc:
        latency = time.monotonic() - start_time
        err_msg = str(exc)
        if "timed out" in err_msg.lower():
            err_msg = "Connection timed out"
        elif "connection refused" in err_msg.lower():
            err_msg = "Connection refused"
        return (False, latency, err_msg)


def get_configured_candidates() -> List[str]:
    """Retrieves configured mirror candidates from global config or defaults."""
    cfg = load_global_config()
    net_cfg = cfg.get("network", {})
    if isinstance(net_cfg, dict):
        py_net = net_cfg.get("python", {})
        if isinstance(py_net, dict):
            candidates = py_net.get("candidates")
            if isinstance(candidates, list) and candidates:
                return [str(c) for c in candidates if str(c).strip()]
    return list(DEFAULT_MIRROR_CANDIDATES)


def persist_selected_mirror(mirror_url: str) -> bool:
    """
    Persists the selected mirror to /etc/deployx/config.yml under build.python and network.python.
    """
    import yaml

    cfg_file = paths.config_dir / "config.yml"
    current = load_global_config()

    if not isinstance(current.get("build"), dict):
        current["build"] = {}
    if not isinstance(current["build"].get("python"), dict):
        current["build"]["python"] = {}

    current["build"]["python"]["index_url"] = mirror_url
    parsed = urlparse(mirror_url)
    if parsed.hostname and parsed.hostname != "pypi.org":
        current["build"]["python"]["trusted_host"] = parsed.hostname
    else:
        current["build"]["python"]["trusted_host"] = None

    if not isinstance(current.get("network"), dict):
        current["network"] = {}
    if not isinstance(current["network"].get("python"), dict):
        current["network"]["python"] = {}
    current["network"]["python"]["selected_mirror"] = mirror_url

    try:
        content = yaml.dump(current, sort_keys=False, default_flow_style=False)
        atomic_write_file(cfg_file, content, mode=0o644)
        return True
    except Exception:
        return False


def auto_select_best_mirror(
    candidates: Optional[List[str]] = None,
    force_retest: bool = False,
    persist: bool = True,
    timeout: float = 3.5,
) -> Tuple[str, Dict[str, Any]]:
    """
    Evaluates official PyPI and candidate mirrors to select the fastest healthy mirror.
    If official PyPI is responsive, it is prioritized.
    Otherwise, picks the candidate with lowest latency.
    """
    candidate_list = candidates or get_configured_candidates()
    probe_results: Dict[str, Dict[str, Any]] = {}

    # 1. Test official PyPI first
    pypi_url = "https://pypi.org/simple/"
    is_pypi_ok, pypi_latency, pypi_status = test_mirror(pypi_url, timeout=timeout)
    probe_results[pypi_url] = {
        "healthy": is_pypi_ok,
        "latency": pypi_latency,
        "status": pypi_status,
    }

    # If official PyPI is fast (< 2.0s), use it directly
    if is_pypi_ok and pypi_latency < 2.0 and not force_retest:
        if persist:
            persist_selected_mirror(pypi_url)
        return pypi_url, probe_results

    # 2. Test other candidates
    healthy_mirrors: List[Tuple[str, float]] = []
    if is_pypi_ok:
        healthy_mirrors.append((pypi_url, pypi_latency))

    for cand in candidate_list:
        if cand == pypi_url:
            continue
        is_ok, lat, stat = test_mirror(cand, timeout=timeout)
        probe_results[cand] = {
            "healthy": is_ok,
            "latency": lat,
            "status": stat,
        }
        if is_ok:
            healthy_mirrors.append((cand, lat))

    if healthy_mirrors:
        # Sort by latency ascending
        healthy_mirrors.sort(key=lambda x: x[1])
        best_url = healthy_mirrors[0][0]
    else:
        # Fallback to official PyPI even if slow/unresponsive
        best_url = pypi_url

    if persist:
        persist_selected_mirror(best_url)

    return best_url, probe_results


class MirrorFailoverManager:
    """
    Manages mirror failovers during Docker builds to recover from
    degraded or unreachable indices without infinite loops.
    """

    def __init__(self, initial_mirror: Optional[str] = None):
        self.candidates = get_configured_candidates()
        self.tried_mirrors: List[str] = []
        if initial_mirror:
            self.tried_mirrors.append(initial_mirror)

    def get_current_mirror(self) -> Optional[str]:
        return self.tried_mirrors[-1] if self.tried_mirrors else None

    def get_next_fallback_mirror(self) -> Optional[str]:
        """
        Returns the next untried healthy mirror candidate, or None if exhausted.
        """
        for cand in self.candidates:
            if cand not in self.tried_mirrors:
                is_ok, _, _ = test_mirror(cand, timeout=2.5)
                self.tried_mirrors.append(cand)
                if is_ok:
                    return cand

        # If all candidates tried, return None to prevent infinite loops
        return None

    def has_remaining_mirrors(self) -> bool:
        return any(c not in self.tried_mirrors for c in self.candidates)
