from __future__ import annotations

import configparser
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INI = ROOT / "miner.ini"

# Shared library name for the native CUDA engine (platform default).
DEFAULT_CUDA_LIB = (
    "native/build/bin/xen_cuda.dll"
    if sys.platform == "win32"
    else "native/build/bin/libxen_cuda.so"
)


@dataclass(frozen=True)
class Settings:
    address: str
    worker: str
    base_url: str
    connection_timeout_s: int
    network_poll_interval_s: int
    network_poll_timeout_s: int
    network_down_poll_interval_s: int
    backend: str
    strategy: str
    memory_cost: int
    time_cost: int
    parallelism: int
    hash_len: int
    # VRAM policy as % of each GPU's total (auto-scales). Absolute overrides
    # below are optional; 0 means "use percentage only".
    target_vram_pct: float
    desktop_headroom_pct: float
    emergency_vram_pct: float
    min_headroom_pct: float
    runtime_overhead_pct: float
    min_headroom_floor_mib: int
    runtime_overhead_floor_mib: int
    target_vram_mib: int
    headroom_mib: int
    emergency_vram_mib: int
    min_headroom_mib: int
    max_gpu_temp_c: int
    warn_gpu_temp_c: int
    gpu_cooldown_s: int
    gpu_power_boost_enabled: bool
    gpu_power_target_pct: int
    gpu_windows_performance_mode: bool
    temp_watch_path: Path
    cpu_lanes: int
    lane_ramp_step: int
    sample_interval_s: int
    db_path: Path
    jsonl_path: Path
    rejected_jsonl_path: Path
    log_path: Path
    timelapse_path: Path
    stats_interval_s: int
    timelapse_sample_s: int
    dashboard_enabled: bool
    woodyminer_enabled: bool
    woodyminer_upload_url: str
    woodyminer_upload_period_s: int
    woodyminer_custom_name: str
    # Share accepts + holdings with local/open XenBlockScan index
    xenblockscan_enabled: bool
    xenblockscan_endpoint: str
    xenblockscan_api_key: str
    xenblockscan_report_rejects: bool
    xenblockscan_holdings_interval_s: int
    xenblockscan_backfill: bool
    # Stable ID for this miner install (website fleet tracker)
    tracker_id: str
    xenblocks_exe: Path | None
    xenblocks_db: Path | None
    gpu_enabled: bool
    cuda_dll_path: Path
    cuda_batch_size: int
    cuda_max_batch_size: int
    cuda_runtime_overhead_mib: int
    vram_reference_difficulty: int
    cuda_max_lanes: int
    cuda_lane_reserve: int
    submit_cpu_fraction: float

    @property
    def salt_hex(self) -> str:
        return self.address[2:]

    @property
    def difficulty_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/difficulty"

    @property
    def verify_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/verify"


def _ensure_tracker_id(ini_path: Path, mon: configparser.SectionProxy) -> str:
    """Stable fleet tracker id for this install; write once to miner.ini."""
    existing = (mon.get("tracker_id") or mon.get("xenblockscan_tracker_id") or "").strip()
    if existing:
        return existing
    tid = f"xbs-{uuid.uuid4().hex[:16]}"
    try:
        from config.wallet_setup import _set_ini_value

        _set_ini_value(ini_path, "monitoring", "tracker_id", tid)
    except Exception:
        pass
    return tid


def load_settings(ini_path: Path | None = None) -> Settings:
    path = ini_path or DEFAULT_INI
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")

    acc = cp["account"]
    srv = cp["server"]
    mine = cp["mining"]
    eff = cp["efficiency"]
    que = cp["queue"]
    mon = cp["monitoring"]
    gpu = cp["gpu"]
    cuda = cp["cuda"] if "cuda" in cp else {}

    exe_raw = gpu.get("xenblocks_exe", "").strip()
    dll_raw = cuda.get("dll_path", DEFAULT_CUDA_LIB).strip()
    # Older configs may still point at the Windows DLL name on Linux.
    if sys.platform != "win32" and dll_raw.endswith("xen_cuda.dll"):
        linux_lib = dll_raw.replace("xen_cuda.dll", "libxen_cuda.so")
        if (ROOT / linux_lib).exists() or not (ROOT / dll_raw).exists():
            dll_raw = linux_lib
    return Settings(
        address=acc.get("address", "").strip(),
        worker=acc.get("worker", "").strip(),
        base_url=srv.get("base_url", "http://xenblocks.io").strip(),
        connection_timeout_s=int(srv.get("connection_timeout_s", "20")),
        network_poll_interval_s=int(srv.get("network_poll_interval_s", "15")),
        network_poll_timeout_s=int(srv.get("network_poll_timeout_s", "3")),
        network_down_poll_interval_s=int(srv.get("network_down_poll_interval_s", "30")),
        backend=mine.get("backend", "cpu").strip().lower(),
        strategy=mine.get("strategy", "random").strip().lower(),
        memory_cost=int(mine.get("memory_cost", "1100")),
        time_cost=int(mine.get("time_cost", "1")),
        parallelism=int(mine.get("parallelism", "1")),
        hash_len=int(mine.get("hash_len", "64")),
        # % of each card's total VRAM (from 5090 safety profile ≈ 69/25/93/4/6).
        target_vram_pct=float(eff.get("target_vram_pct", "69.09")),
        desktop_headroom_pct=float(eff.get("desktop_headroom_pct", "25.12")),
        emergency_vram_pct=float(eff.get("emergency_vram_pct", "92.78")),
        min_headroom_pct=float(eff.get("min_headroom_pct", "3.68")),
        runtime_overhead_pct=float(
            cuda.get(
                "runtime_overhead_pct",
                eff.get("runtime_overhead_pct", "6.28"),
            )
        ),
        min_headroom_floor_mib=int(eff.get("min_headroom_floor_mib", "512")),
        runtime_overhead_floor_mib=int(
            cuda.get(
                "runtime_overhead_floor_mib",
                eff.get("runtime_overhead_floor_mib", "256"),
            )
        ),
        # Absolute overrides (0 = derive from % of detected total VRAM).
        target_vram_mib=int(eff.get("target_vram_mib", "0")),
        headroom_mib=int(eff.get("headroom_mib", "0")),
        emergency_vram_mib=int(eff.get("emergency_vram_mib", "0")),
        min_headroom_mib=int(eff.get("min_headroom_mib", "0")),
        max_gpu_temp_c=int(eff.get("max_gpu_temp_c", "75")),
        warn_gpu_temp_c=int(eff.get("warn_gpu_temp_c", "72")),
        gpu_cooldown_s=int(eff.get("gpu_cooldown_s", "45")),
        gpu_power_boost_enabled=eff.getboolean("gpu_power_boost_enabled", fallback=True),
        gpu_power_target_pct=int(eff.get("gpu_power_target_pct", "100")),
        gpu_windows_performance_mode=eff.getboolean(
            "gpu_windows_performance_mode",
            # Windows powercfg High Performance is a no-op on Linux.
            fallback=sys.platform == "win32",
        ),
        temp_watch_path=ROOT / mon.get("temp_watch_path", "data/temp_watch.log"),
        cpu_lanes=int(eff.get("cpu_lanes", "2")),
        lane_ramp_step=int(eff.get("lane_ramp_step", "1")),
        sample_interval_s=int(eff.get("sample_interval_s", "5")),
        db_path=ROOT / que.get("db_path", "data/blocks.db"),
        jsonl_path=ROOT / que.get("jsonl_path", "data/queue.jsonl"),
        rejected_jsonl_path=ROOT / que.get("rejected_jsonl_path", "data/rejected.jsonl"),
        log_path=ROOT / mon.get("log_path", "data/session.log"),
        timelapse_path=ROOT / mon.get("timelapse_path", "data/session_timelapse.jsonl"),
        stats_interval_s=int(mon.get("stats_interval_s", "10")),
        timelapse_sample_s=int(mon.get("timelapse_sample_s", "30")),
        dashboard_enabled=mon.getboolean("dashboard_enabled", fallback=True),
        woodyminer_enabled=mon.getboolean("woodyminer_enabled", fallback=True),
        woodyminer_upload_url=mon.get(
            "woodyminer_upload_url", "https://woodyminer.com/api/stat/upload"
        ).strip(),
        woodyminer_upload_period_s=int(mon.get("woodyminer_upload_period_s", "60")),
        woodyminer_custom_name=mon.get("woodyminer_custom_name", "").strip()
        or acc.get("worker", "").strip(),
        xenblockscan_enabled=mon.getboolean("xenblockscan_enabled", fallback=False),
        xenblockscan_endpoint=mon.get(
            "xenblockscan_endpoint", "http://127.0.0.1:8787/api/v1/events"
        ).strip(),
        xenblockscan_api_key=mon.get("xenblockscan_api_key", "").strip(),
        xenblockscan_report_rejects=mon.getboolean(
            "xenblockscan_report_rejects", fallback=False
        ),
        # How often to push balances + live hashrate to the site (seconds)
        xenblockscan_holdings_interval_s=int(
            mon.get("xenblockscan_holdings_interval_s", "30")
        ),
        # Off by default — history bulk was hanging startup; live accepts are enough
        xenblockscan_backfill=mon.getboolean("xenblockscan_backfill", fallback=False),
        tracker_id=_ensure_tracker_id(path, mon),
        xenblocks_exe=Path(exe_raw) if exe_raw else None,
        xenblocks_db=Path(gpu.get("xenblocks_db", "").strip()) if gpu.get("xenblocks_db", "").strip() else None,
        gpu_enabled=gpu.getboolean("enabled", fallback=False),
        cuda_dll_path=ROOT / dll_raw,
        cuda_batch_size=int(cuda.get("batch_size", "0")),
        cuda_max_batch_size=int(cuda.get("max_batch_size", "0")),
        # 0 = use runtime_overhead_pct of GPU total.
        cuda_runtime_overhead_mib=int(
            cuda.get("runtime_overhead_mib", eff.get("cuda_runtime_overhead_mib", "0"))
        ),
        vram_reference_difficulty=int(
            cuda.get(
                "vram_reference_difficulty",
                mine.get("memory_cost", "1100"),
            )
        ),
        cuda_max_lanes=int(cuda.get("max_lanes", "4")),
        cuda_lane_reserve=int(cuda.get("lane_reserve", "1")),
        submit_cpu_fraction=float(que.get("submit_cpu_fraction", "0.30")),
    )