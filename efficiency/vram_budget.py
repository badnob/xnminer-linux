from __future__ import annotations

from core.models import GpuSnapshot


class VramBudget:
    def __init__(self, target_mib: int, headroom_mib: int) -> None:
        self.target_mib = target_mib
        self.headroom_mib = headroom_mib

    def over_budget(self, snap: GpuSnapshot | None) -> bool:
        if snap is None:
            return False
        return snap.used_mib > self.target_mib

    def can_add_lane(self, snap: GpuSnapshot | None, est_lane_mib: int = 1024) -> bool:
        if snap is None:
            return True
        projected = snap.used_mib + est_lane_mib
        return projected <= self.target_mib and snap.headroom_mib >= self.headroom_mib

    def should_reduce(self, snap: GpuSnapshot | None) -> bool:
        if snap is None:
            return False
        return snap.used_mib > self.target_mib or snap.headroom_mib < self.headroom_mib // 2