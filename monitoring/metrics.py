from __future__ import annotations

import time

from core.models import GpuSnapshot, MiningStats

_BLOCK_KINDS = ("XUNI", "XNM", "XBLK")


def _norm_kind(kind: str) -> str:
    upper = (kind or "").upper()
    if upper in _BLOCK_KINDS:
        return upper
    return "XBLK"


def _inc(stats: MiningStats, field: str, kind: str) -> None:
    key = f"{field}_{_norm_kind(kind).lower()}"
    setattr(stats, key, getattr(stats, key) + 1)


class MetricsTracker:
    """Rolling hash-rate and session counters."""

    def __init__(self, ema_alpha: float = 0.3) -> None:
        self.stats = MiningStats()
        self._last_tick = time.perf_counter()
        self._alpha = ema_alpha
        self._rejected_live_keys: dict[str, set[str]] = {k: set() for k in _BLOCK_KINDS}
        self._rejected_flush_keys: dict[str, set[str]] = {k: set() for k in _BLOCK_KINDS}

    def record_hashes(self, count: int, lanes: int) -> None:
        now = time.perf_counter()
        elapsed = max(now - self._last_tick, 1e-9)
        self._last_tick = now
        self.stats.total_hashes += count
        self.stats.active_lanes = lanes
        instant = count / elapsed
        if self.stats.hps_ema <= 0:
            self.stats.hps_ema = instant
        else:
            self.stats.hps_ema = (
                self._alpha * instant + (1 - self._alpha) * self.stats.hps_ema
            )

    def record_found(self, kind: str) -> None:
        self.stats.session_hits += 1
        _inc(self.stats, "found", kind)

    def record_enqueued(self, kind: str) -> None:
        _inc(self.stats, "enqueued", kind)

    def record_accepted_live(self, kind: str) -> None:
        _inc(self.stats, "accepted_live", kind)

    def record_accepted_flush(self, kind: str) -> None:
        _inc(self.stats, "accepted_flush", kind)

    def record_failed_live(self, kind: str) -> None:
        _inc(self.stats, "failed_live", kind)

    def record_rejected_live(self, kind: str, block_key: str) -> None:
        norm = _norm_kind(kind)
        if block_key in self._rejected_live_keys[norm]:
            return
        self._rejected_live_keys[norm].add(block_key)
        _inc(self.stats, "rejected_live", kind)
        _inc(self.stats, "failed_live", kind)

    def record_rejected_flush(self, kind: str, block_key: str) -> None:
        norm = _norm_kind(kind)
        if block_key in self._rejected_flush_keys[norm]:
            return
        self._rejected_flush_keys[norm].add(block_key)
        _inc(self.stats, "rejected_flush", kind)

    def record_resubmission(self, kind: str) -> None:
        _inc(self.stats, "resubmission", kind)

    def record_dropped_flush(self) -> None:
        self.stats.dropped_flush += 1

    def sync_pending(self, count: int) -> None:
        self.stats.queued = count

    def summary_line(self, gpu: GpuSnapshot | None) -> str:
        s = self.stats
        gpu_part = "GPU=n/a"
        if gpu:
            gpu_part = (
                f"VRAM={gpu.used_mib}/{gpu.total_mib}MiB "
                f"headroom={gpu.headroom_mib}MiB util={gpu.util_pct}%"
            )
        return (
            f"lanes={s.active_lanes} "
            f"hps={s.hps_ema:,.0f} "
            f"total={s.total_hashes:,} "
            f"found={s.found_total} "
            f"accepted={s.accepted_total} "
            f"(live={s.accepted_live_total} flush={s.accepted_flush_total}) "
            f"queue_db={s.queued} | {gpu_part}"
        )