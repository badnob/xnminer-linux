from __future__ import annotations

from config.settings import Settings
from mining.argon2_common import verify_known_block
from monitoring.nvidia import NvidiaMonitor
from networking.health import check_port80


def run_diagnostics(settings: Settings) -> dict:
    gpu = NvidiaMonitor(device_index=0)
    snap = gpu.snapshot()
    port80_up = check_port80(settings.base_url, timeout_s=3.0)
    return {
        "calibration_m100": verify_known_block(settings.salt_hex, memory_cost=100),
        "memory_cost_config": settings.memory_cost,
        "port80_tcp": port80_up,
        "difficulty_fetch": {
            "up": port80_up,
            "difficulty": None,
            "latency_ms": None,
            "error": None if port80_up else "port 80 unreachable (difficulty checked after start)",
        },
        "gpu": None if not snap else {
            "name": snap.name,
            "total_mib": snap.total_mib,
            "used_mib": snap.used_mib,
            "headroom_mib": snap.headroom_mib,
        },
        "backend": settings.backend,
        "strategy": settings.strategy,
        "target_vram_mib": settings.target_vram_mib,
    }