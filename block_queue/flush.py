from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from config.settings import Settings
from core.models import BlockHit
from mining.argon2_common import memory_cost_from_hash, prepare_hit_for_submit
from monitoring.logger import SessionLogger
from monitoring.metrics import MetricsTracker
from networking.submit_result import (
    counts_as_reject,
    is_difficulty_mismatch,
    is_xuni_window_reject,
    submit_response_hint,
)
from networking.submitter import Submitter
from block_queue.policy import XUNI_WINDOW_LABEL, ready_to_flush
from block_queue.store import (
    DIFFICULTY_CHANGE_REASON,
    OUTSIDE_XUNI_WINDOW_REASON,
    RESUBMISSION_REASON,
    SHUTDOWN_PENDING_REASON,
    BlockStore,
)
from block_queue.submit_cpu import SubmitCpuPool
from monitoring import xenblockscan_reporter as xbs

if TYPE_CHECKING:
    from monitoring.local_stats import LocalMiningStatsTracker


@dataclass
class _FlushRowOutcome:
    row_id: int
    kind: str = ""
    key: str = ""
    hash_str: str = ""
    flushed: bool = False
    dropped: bool = False
    waiting_xuni: bool = False
    waiting_difficulty: bool = False
    block_memory_cost: int | None = None
    shutdown_queued: bool = False
    counted_reject: bool = False
    warn: str = ""


class QueueFlusher:
    def __init__(
        self,
        store: BlockStore,
        submitter: Submitter,
        logger: SessionLogger,
        settings: Settings,
        get_difficulty: Callable[[], int],
        metrics: MetricsTracker | None = None,
        local_stats: LocalMiningStatsTracker | None = None,
        submit_pool: SubmitCpuPool | None = None,
    ) -> None:
        self.store = store
        self.submitter = submitter
        self.logger = logger
        self.settings = settings
        self.get_difficulty = get_difficulty
        self.metrics = metrics
        self.local_stats = local_stats
        self.submit_pool = submit_pool or SubmitCpuPool(settings.submit_cpu_fraction)
        self._warned_flush_keys: set[str] = set()
        self._warn_lock = threading.Lock()

    def _prepare_row(self, row: dict, *, on_shutdown: bool) -> _FlushRowOutcome:
        hit = BlockHit(
            key=row["key"],
            hash_str=row["hash_str"],
            block_type=row["block_type"],
            attempts=row["attempts"],
            strategy=row["strategy"],
            hps=row["hps"],
        )
        current_difficulty = self.get_difficulty()
        block_memory_cost = row.get("memory_cost") or memory_cost_from_hash(hit.hash_str)
        if (
            block_memory_cost is not None
            and int(block_memory_cost) != current_difficulty
        ):
            return _FlushRowOutcome(
                row_id=row["id"],
                waiting_difficulty=True,
                key=hit.key,
                kind=hit.block_type,
                block_memory_cost=int(block_memory_cost),
            )

        memory_cost = block_memory_cost or current_difficulty
        prepared = prepare_hit_for_submit(
            hit,
            salt_hex=self.settings.salt_hex,
            memory_cost=int(memory_cost),
            time_cost=self.settings.time_cost,
            parallelism=1,
            hash_len=self.settings.hash_len,
        )
        if prepared is None:
            self.store.mark_submitted(row["id"], 0, "filtered:false_positive")
            return _FlushRowOutcome(row_id=row["id"], dropped=True, key=hit.key)

        hit = prepared
        ok, _why = ready_to_flush(hit.block_type)
        if not ok:
            if on_shutdown:
                self.store.mark_pending_reason(row["id"], OUTSIDE_XUNI_WINDOW_REASON)
            return _FlushRowOutcome(
                row_id=row["id"],
                waiting_xuni=True,
                key=hit.key,
                kind=hit.block_type,
            )

        timeout_s = 5 if on_shutdown else 20
        result = self.submitter.submit(hit, timeout_s=timeout_s)
        if result["ok"]:
            self.store.mark_submitted(row["id"], result["status"], result["body"])
            hint = submit_response_hint(result["status"], result["body"])
            warn = ""
            if "duplicate" in hint:
                warn = (
                    f"Flushed {hit.block_type} key={hit.key[:16]}... ({hint})"
                )
            return _FlushRowOutcome(
                row_id=row["id"],
                flushed=True,
                key=hit.key,
                hash_str=hit.hash_str or "",
                kind=hit.block_type,
                block_memory_cost=int(memory_cost) if memory_cost else None,
                warn=warn,
            )

        status = int(result["status"])
        body = str(result.get("body") or "")
        hint = submit_response_hint(status, body)

        # Hold for matching conditions — not a permanent reject.
        if is_difficulty_mismatch(status, body):
            self.store.mark_pending_reason(row["id"], DIFFICULTY_CHANGE_REASON)
            return _FlushRowOutcome(
                row_id=row["id"],
                waiting_difficulty=True,
                key=hit.key,
                kind=hit.block_type,
                block_memory_cost=int(memory_cost) if memory_cost else None,
            )
        if is_xuni_window_reject(status, body):
            self.store.mark_pending_reason(row["id"], OUTSIDE_XUNI_WINDOW_REASON)
            return _FlushRowOutcome(
                row_id=row["id"],
                waiting_xuni=True,
                key=hit.key,
                kind=hit.block_type,
            )

        fail_reason = None
        shutdown_queued = False
        if on_shutdown and (hit.block_type or "").upper() in ("XNM", "XBLK"):
            fail_reason = SHUTDOWN_PENDING_REASON
            shutdown_queued = True

        # Genuine pool rejects go to resubmission; network blips stay pending
        # without polluting rejected.jsonl / reject counters / woodyminer.
        if counts_as_reject(status, body):
            self.store.record_rejection(
                hit,
                status,
                body,
                "flush",
                pending_id=row["id"],
                queue_reason=fail_reason or RESUBMISSION_REASON,
            )
            counted_reject = True
        else:
            if fail_reason is not None:
                self.store.mark_pending_reason(row["id"], fail_reason)
            counted_reject = False

        retry_note = (
            "queued for next start" if on_shutdown else "kept in queue for retry"
        )
        warn = (
            f"Flush failed {hit.block_type} key={hit.key[:16]}... "
            f"({hint}) — {retry_note}"
        )
        return _FlushRowOutcome(
            row_id=row["id"],
            key=hit.key,
            kind=hit.block_type,
            shutdown_queued=shutdown_queued,
            counted_reject=counted_reject,
            warn=warn,
        )

    def _apply_outcome(self, outcome: _FlushRowOutcome, *, on_shutdown: bool) -> int:
        flushed = 0
        if outcome.dropped:
            self.logger.warn(
                f"Queued block dropped (false positive): key={outcome.key[:16]}..."
            )
            if self.metrics:
                self.metrics.record_dropped_flush()
            return flushed

        if outcome.flushed:
            if self.metrics:
                self.metrics.record_accepted_flush(outcome.kind)
            if self.local_stats:
                self.local_stats.record_accept(outcome.kind)
            if outcome.warn:
                self.logger.info(outcome.warn)
            # Share flush accepts (XUNI window + queue) with XenBlockScan
            try:
                if self.settings.xenblockscan_enabled and self.settings.address:
                    xbs.report_accepted(
                        account=self.settings.address,
                        kind=outcome.kind or "XNM",
                        key=outcome.key or "",
                        hash_to_verify=outcome.hash_str or "",
                        worker=self.settings.worker or "",
                        difficulty=outcome.block_memory_cost,
                    )
            except Exception as exc:
                self.logger.warn(f"xenblockscan flush report failed: {exc}")
            return 1

        if outcome.warn:
            with self._warn_lock:
                if outcome.key not in self._warned_flush_keys:
                    self._warned_flush_keys.add(outcome.key)
                    self.logger.warn(outcome.warn)
            if self.metrics and outcome.counted_reject:
                self.metrics.record_rejected_flush(outcome.kind, outcome.key)
        return flushed

    def flush(self, *, on_shutdown: bool = False) -> int:
        """Submit queued blocks. Caller must confirm network is up before calling."""
        rows = list(self.store.pending())
        if not rows:
            return 0

        outcomes = self.submit_pool.map(
            lambda row: self._prepare_row(row, on_shutdown=on_shutdown),
            rows,
        )

        flushed = 0
        waiting_xuni = 0
        waiting_difficulty = 0
        held_difficulties: set[int] = set()
        shutdown_queued = 0
        for outcome in outcomes:
            if outcome.waiting_difficulty:
                waiting_difficulty += 1
                if outcome.block_memory_cost is not None:
                    held_difficulties.add(outcome.block_memory_cost)
                continue
            if outcome.waiting_xuni:
                waiting_xuni += 1
                continue
            if outcome.shutdown_queued:
                shutdown_queued += 1
            flushed += self._apply_outcome(outcome, on_shutdown=on_shutdown)

        if waiting_difficulty:
            held = ",".join(str(m) for m in sorted(held_difficulties))
            self.logger.info(
                f"{waiting_difficulty} queued block(s) held until difficulty "
                f"matches (block m={held or '?'}, network m={self.get_difficulty()})"
            )
        if waiting_xuni:
            if on_shutdown:
                self.logger.info(
                    f"{waiting_xuni} queued XUNI block(s) held for next start "
                    f"(outside {XUNI_WINDOW_LABEL} window)"
                )
            else:
                self.logger.info(
                    f"{waiting_xuni} queued XUNI block(s) held until window "
                    f"({XUNI_WINDOW_LABEL})"
                )
        if shutdown_queued:
            self.logger.info(
                f"{shutdown_queued} queued XNM/XBLK block(s) held for next start "
                f"(could not submit before exit)"
            )
        if flushed:
            self.logger.info(f"Flushed {flushed} queued block(s)")
        remaining = self.store.pending_count()
        if remaining:
            self.logger.info(f"{remaining} block(s) still pending in queue")
        return flushed

    def defer_to_next_start(self) -> int:
        """Mark pending blocks for the next session without contacting the server."""
        deferred = 0
        xuni_held = 0
        shutdown_queued = 0
        for row in self.store.pending():
            kind = (row["block_type"] or "").upper()
            ok, _ = ready_to_flush(kind)
            if kind == "XUNI" and not ok:
                self.store.mark_pending_reason(row["id"], OUTSIDE_XUNI_WINDOW_REASON)
                xuni_held += 1
            else:
                self.store.mark_pending_reason(row["id"], SHUTDOWN_PENDING_REASON)
                shutdown_queued += 1
            deferred += 1

        if xuni_held:
            self.logger.info(
                f"{xuni_held} queued XUNI block(s) held for next start "
                f"(outside {XUNI_WINDOW_LABEL} window)"
            )
        if shutdown_queued:
            self.logger.info(
                f"{shutdown_queued} queued block(s) held for next start "
                f"(network down on shutdown)"
            )
        remaining = self.store.pending_count()
        if remaining:
            self.logger.info(f"{remaining} block(s) still pending in queue")
        return deferred