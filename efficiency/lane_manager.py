from __future__ import annotations

from core.models import GpuSnapshot
from efficiency.vram_budget import VramBudget
from mining.base import MinerBackend
from monitoring.logger import SessionLogger


class LaneManager:
    def __init__(
        self,
        backend: MinerBackend,
        budget: VramBudget,
        initial_lanes: int,
        ramp_step: int,
        logger: SessionLogger,
        enabled: bool = True,
    ) -> None:
        self.backend = backend
        self.budget = budget
        self.lanes = max(1, initial_lanes)
        self.ramp_step = max(1, ramp_step)
        self.logger = logger
        self.enabled = enabled
        if self.enabled:
            self.backend.set_lanes(self.lanes)

    def tune(self, gpu: GpuSnapshot | None) -> int:
        if not self.enabled:
            return self.lanes
        if self.budget.should_reduce(gpu):
            new_lanes = max(1, self.lanes - self.ramp_step)
            if new_lanes != self.lanes:
                self.logger.warn(
                    f"VRAM pressure — lanes {self.lanes} -> {new_lanes} "
                    f"(used={getattr(gpu, 'used_mib', '?')}MiB)"
                )
                self.lanes = new_lanes
                self.backend.set_lanes(self.lanes)
            return self.lanes

        if self.budget.can_add_lane(gpu):
            new_lanes = self.lanes + self.ramp_step
            self.logger.info(f"VRAM headroom OK — lanes {self.lanes} -> {new_lanes}")
            self.lanes = new_lanes
            self.backend.set_lanes(self.lanes)
        return self.lanes