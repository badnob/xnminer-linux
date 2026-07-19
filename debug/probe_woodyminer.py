"""Probe woodyminer stat upload API responses."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import load_settings
from core.models import GpuSnapshot, MiningStats
from monitoring.woodyminer_stats import build_stat_payload, derive_machine_id


def try_upload(user_agent: str | None) -> None:
    settings = load_settings()
    stats = MiningStats(hps_ema=100000.0, total_hashes=1000, accepted_live_xnm=1)
    gpu = GpuSnapshot(0, "RTX", 24576, 12000, 12576, 90, 200.0, 60)
    payload = build_stat_payload(
        machine_id=derive_machine_id(0),
        miner_address=settings.address,
        stats=stats,
        gpu=gpu,
        difficulty=1100,
        uptime_s=120,
        custom_name=settings.woodyminer_custom_name,
        version="1.4.0",
    )
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if user_agent:
        headers["User-Agent"] = user_agent
    req = urllib.request.Request(
        settings.woodyminer_upload_url,
        data=data,
        headers=headers,
        method="POST",
    )
    label = user_agent or "<default urllib>"
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(f"[{label}] status={resp.status} body={body[:300]}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"[{label}] status={exc.code} body={body[:500]}")
        print(f"headers={dict(exc.headers)}")
    except Exception as exc:
        print(f"[{label}] error={exc}")


if __name__ == "__main__":
    for ua in [
        None,
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "xenblocksMiner/1.4.0",
        "curl/8.0",
    ]:
        try_upload(ua)