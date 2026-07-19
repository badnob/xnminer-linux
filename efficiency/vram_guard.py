from __future__ import annotations

from dataclasses import dataclass

from core.models import GpuSnapshot


@dataclass
class GuardAction:
    level: str  # ok | warn | emergency
    message: str
    code: str = "ok"
    should_stop_gpu: bool = False
    graceful_stop: bool = False
    cooldown_s: int = 0


class VramGuard:
    """Hardware safety: emergency stop before CUDA OOM."""

    def __init__(
        self,
        target_vram_mib: int,
        desktop_headroom_mib: int,
        emergency_vram_mib: int,
        min_headroom_mib: int,
        max_temp_c: int,
        warn_temp_c: int,
        cooldown_s: int,
    ) -> None:
        self.target_vram_mib = target_vram_mib
        self.desktop_headroom_mib = desktop_headroom_mib
        self.emergency_vram_mib = emergency_vram_mib
        self.min_headroom_mib = min_headroom_mib
        self.max_temp_c = max_temp_c
        self.warn_temp_c = min(warn_temp_c, max_temp_c - 1)
        self.cooldown_s = cooldown_s

    def evaluate(self, snap: GpuSnapshot | None) -> GuardAction:
        if snap is None:
            return GuardAction("ok", "GPU metrics unavailable")

        emergency_limit = min(self.emergency_vram_mib, max(snap.total_mib - 256, 0))
        if snap.used_mib >= emergency_limit:
            return GuardAction(
                "emergency",
                f"VRAM emergency: {snap.used_mib}MiB >= {emergency_limit}MiB",
                code="vram_emergency",
                should_stop_gpu=True,
                cooldown_s=self.cooldown_s,
            )

        if snap.headroom_mib <= self.min_headroom_mib:
            return GuardAction(
                "emergency",
                f"Headroom critical: {snap.headroom_mib}MiB <= {self.min_headroom_mib}MiB",
                code="headroom_critical",
                should_stop_gpu=True,
                cooldown_s=self.cooldown_s,
            )

        if snap.temperature_c >= self.max_temp_c:
            return GuardAction(
                "emergency",
                f"GPU temp {snap.temperature_c}C >= {self.max_temp_c}C",
                code="gpu_temp_emergency",
                should_stop_gpu=True,
                graceful_stop=True,
                cooldown_s=self.cooldown_s,
            )

        if snap.temperature_c >= self.warn_temp_c:
            return GuardAction(
                "warn",
                f"GPU temp {snap.temperature_c}C >= warn {self.warn_temp_c}C",
                code="gpu_temp_warn",
            )

        if snap.headroom_mib < self.desktop_headroom_mib:
            return GuardAction(
                "warn",
                f"Desktop VRAM low: {snap.headroom_mib}MiB free "
                f"(need {self.desktop_headroom_mib}MiB for console/desktop)",
                code="desktop_vram_low",
            )

        return GuardAction(
            "ok",
            f"VRAM {snap.used_mib}MiB headroom {snap.headroom_mib}MiB",
            code="ok",
        )