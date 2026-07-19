from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from core.models import NetworkStatus


def accept_network_difficulty(diff: int, *, fallback: int) -> int:
    """Use the live server value; memory_cost is only a fallback when RPC is invalid."""
    if diff <= 0:
        return fallback
    return diff


def fetch_difficulty(url: str, timeout_s: int = 20) -> NetworkStatus:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            latency = (time.perf_counter() - t0) * 1000
            data = json.loads(body)
            diff = int(data.get("difficulty", data.get("diff", 0)))
            return NetworkStatus(
                port80_up=True,
                difficulty=diff,
                latency_ms=latency,
            )
    except urllib.error.HTTPError as exc:
        return NetworkStatus(
            port80_up=True,
            difficulty=None,
            latency_ms=None,
            error=f"HTTP {exc.code}",
        )
    except Exception as exc:
        return NetworkStatus(
            port80_up=False,
            difficulty=None,
            latency_ms=None,
            error=str(exc),
        )