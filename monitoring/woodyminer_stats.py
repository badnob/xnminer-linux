"""Upload mining stats to the woodyminer.com leaderboard API."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Callable

from core.models import GpuSnapshot, MiningStats
from monitoring.logger import SessionLogger

DEFAULT_UPLOAD_URL = "https://woodyminer.com/api/stat/upload"
MINER_VERSION = "3.0.0"
UPLOAD_USER_AGENT = "xenblocksMiner/1.4.0"


def derive_machine_id(device_index: int = 0) -> str:
    """Match woodysoil/XenblocksMiner: sha256(mac + device_list)[:16]."""
    node = uuid.getnode()
    mac = ":".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))
    device_info = f"{device_index},"
    digest = hashlib.sha256(f"{mac}{device_info}".encode("utf-8")).hexdigest()
    return digest[:16]


def build_stat_payload(
    *,
    machine_id: str,
    miner_address: str,
    stats: MiningStats,
    gpu: GpuSnapshot | None,
    difficulty: int,
    uptime_s: int,
    version: str = MINER_VERSION,
    custom_name: str = "",
) -> dict:
    """Build JSON body matching native StatReporter::getStatData()."""
    total_hashrate = stats.hps_ema
    total_hash_count = stats.total_hashes
    total_power_mw = -1

    gpu_entries: list[dict] = []
    if gpu is not None:
        using_pct = 0.0
        if gpu.total_mib > 0:
            using_pct = (gpu.used_mib / gpu.total_mib) * 100.0
        if gpu.power_w >= 0:
            total_power_mw = int(gpu.power_w * 1000)
        gpu_entries.append(
            {
                "index": gpu.index,
                "name": gpu.name,
                "hashrate": f"{total_hashrate:.2f}",
                "memory": gpu.total_mib,
                "power": total_power_mw,
                "utiliz": gpu.util_pct,
                "usingMemory": f"{using_pct:.1f}",
                "hashCount": total_hash_count,
            }
        )

    normal_blocks = stats.accepted_live_xnm + stats.accepted_flush_xnm
    super_blocks = stats.accepted_live_xblk + stats.accepted_flush_xblk
    payload = {
        "machineId": machine_id,
        "minerAddr": miner_address,
        "totalHashrate": f"{total_hashrate:.2f}",
        "totalHashCount": total_hash_count,
        "totalPower": total_power_mw if total_power_mw >= 0 else 0,
        "difficulty": difficulty,
        "gpus": gpu_entries,
        "uptime": uptime_s,
        "acceptedBlocks": normal_blocks + super_blocks,
        "normalBlocks": normal_blocks,
        "superBlocks": super_blocks,
        "rejectedBlocks": stats.rejected_total,
        "version": version,
    }
    if custom_name:
        payload["customName"] = custom_name
    return payload


class WoodyminerStatsUploader:
    """Background thread that POSTs stats to woodyminer.com every N seconds."""

    def __init__(
        self,
        *,
        upload_url: str,
        upload_period_s: int,
        custom_name: str,
        miner_address: str,
        machine_id: str,
        get_stats: Callable[[], MiningStats],
        get_gpu: Callable[[], GpuSnapshot | None],
        get_difficulty: Callable[[], int],
        session_started_at: float,
        logger: SessionLogger,
        version: str = MINER_VERSION,
        timeout_s: float = 3.0,
    ) -> None:
        self.upload_url = upload_url
        self.upload_period_s = max(15, upload_period_s)
        self.custom_name = custom_name.strip()
        self.miner_address = miner_address
        self.machine_id = machine_id
        self.get_stats = get_stats
        self.get_gpu = get_gpu
        self.get_difficulty = get_difficulty
        self.session_started_at = session_started_at
        self.logger = logger
        self.version = version
        self.timeout_s = timeout_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="woodyminer-stats",
            daemon=True,
        )
        self._thread.start()
        self.logger.info(
            f"Woodyminer leaderboard upload enabled "
            f"(machineId={self.machine_id}, every {self.upload_period_s}s)"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def build_payload(self) -> dict:
        uptime_s = max(0, int(time.time() - self.session_started_at))
        return build_stat_payload(
            machine_id=self.machine_id,
            miner_address=self.miner_address,
            stats=self.get_stats(),
            gpu=self.get_gpu(),
            difficulty=self.get_difficulty(),
            uptime_s=uptime_s,
            version=self.version,
            custom_name=self.custom_name,
        )

    def upload_once(self) -> tuple[int, str]:
        payload = self.build_payload()
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.upload_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": UPLOAD_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return exc.code, body
        except Exception as exc:
            return 0, str(exc)

    def _run(self) -> None:
        time.sleep(10)
        period_s = self.upload_period_s
        original_period_s = period_s
        failure_count = 0
        logged_failure = False

        logged_success = False
        while not self._stop.is_set():
            status, body = self.upload_once()
            if status == 201:
                failure_count = 0
                period_s = original_period_s
                logged_failure = False
                if not logged_success:
                    self.logger.info("Woodyminer stat upload OK — leaderboard updated")
                    logged_success = True
            else:
                failure_count += 1
                if not logged_failure:
                    detail = body.replace("\n", " ")[:160]
                    self.logger.warn(
                        f"Woodyminer stat upload failed (HTTP {status}) — "
                        f"{detail} — will retry with backoff"
                    )
                    logged_failure = True
                if failure_count >= 10:
                    period_s = min(period_s * 2, 600)

            if self._stop.wait(period_s):
                break