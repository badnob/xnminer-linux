from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class BlockHit:
    key: str
    hash_str: str
    block_type: str
    attempts: int
    strategy: str
    hps: float = 0.0
    found_at: datetime = field(default_factory=datetime.now)
    memory_cost: int | None = None

    def to_submit_payload(self, account: str, worker: str) -> dict[str, str]:
        return {
            "account": account,
            "key": self.key,
            "hash_to_verify": self.hash_str,
            "attempts": str(self.attempts),
            "hashes_per_second": str(self.hps),
            "worker": worker,
        }


@dataclass
class GpuSnapshot:
    index: int
    name: str
    total_mib: int
    used_mib: int
    free_mib: int
    util_pct: int
    power_w: float
    temperature_c: int

    @property
    def headroom_mib(self) -> int:
        return self.total_mib - self.used_mib


@dataclass
class NetworkStatus:
    port80_up: bool
    difficulty: int | None
    latency_ms: float | None
    error: str | None = None


@dataclass
class MiningStats:
    total_hashes: int = 0
    session_hits: int = 0
    active_lanes: int = 0
    hps_ema: float = 0.0
    found_xuni: int = 0
    found_xnm: int = 0
    found_xblk: int = 0
    enqueued_xuni: int = 0
    enqueued_xnm: int = 0
    enqueued_xblk: int = 0
    accepted_live_xuni: int = 0
    accepted_live_xnm: int = 0
    accepted_live_xblk: int = 0
    accepted_flush_xuni: int = 0
    accepted_flush_xnm: int = 0
    accepted_flush_xblk: int = 0
    failed_live_xuni: int = 0
    failed_live_xnm: int = 0
    failed_live_xblk: int = 0
    rejected_flush_xuni: int = 0
    rejected_flush_xnm: int = 0
    rejected_flush_xblk: int = 0
    rejected_live_xuni: int = 0
    rejected_live_xnm: int = 0
    rejected_live_xblk: int = 0
    resubmission_xuni: int = 0
    resubmission_xnm: int = 0
    resubmission_xblk: int = 0
    dropped_flush: int = 0
    queued: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def found_total(self) -> int:
        return self.found_xuni + self.found_xnm + self.found_xblk

    @property
    def enqueued_total(self) -> int:
        return self.enqueued_xuni + self.enqueued_xnm + self.enqueued_xblk

    @property
    def accepted_live_total(self) -> int:
        return self.accepted_live_xuni + self.accepted_live_xnm + self.accepted_live_xblk

    @property
    def accepted_flush_total(self) -> int:
        return self.accepted_flush_xuni + self.accepted_flush_xnm + self.accepted_flush_xblk

    @property
    def accepted_total(self) -> int:
        return self.accepted_live_total + self.accepted_flush_total

    @property
    def failed_live_total(self) -> int:
        return self.failed_live_xuni + self.failed_live_xnm + self.failed_live_xblk

    @property
    def rejected_flush_total(self) -> int:
        return (
            self.rejected_flush_xuni + self.rejected_flush_xnm + self.rejected_flush_xblk
        )

    @property
    def rejected_live_total(self) -> int:
        return self.rejected_live_xuni + self.rejected_live_xnm + self.rejected_live_xblk

    @property
    def rejected_total(self) -> int:
        return self.rejected_live_total + self.rejected_flush_total

    @property
    def resubmission_total(self) -> int:
        return self.resubmission_xuni + self.resubmission_xnm + self.resubmission_xblk