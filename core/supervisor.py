from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

from config.settings import Settings
from core.instance_lock import InstanceLock
from core.models import BlockHit
from debug.diagnostics import run_diagnostics
from efficiency.cuda_lane_policy import append_lane_event, record_temp_lane_reduction
from efficiency.gpu_power import GpuPowerBooster
from efficiency.lane_manager import LaneManager
from efficiency.vram_budget import VramBudget
from efficiency.vram_guard import VramGuard
from efficiency.vram_policy import VramCaps, resolve_vram_caps
from mining.backends.cpu import CpuArgon2Backend
from mining.backends.cuda_native import CudaNativeBackend
from mining.backends.xenblocks_gpu import XenblocksGpuBackend
from mining.argon2_common import prepare_hit_for_submit
from mining.block_types import classify_block
from mining.xenblocks_watcher import XenblocksDbWatcher
from monitoring.dashboard import MinerDashboard
from monitoring.logger import SessionLogger
from monitoring.metrics import MetricsTracker
from monitoring.timelapse import SessionTimelapse
from monitoring.local_stats import LocalMiningStatsTracker
from monitoring.server_uptime import ServerUptimeTracker
from monitoring.wallet_balances import WalletBalanceTracker
from monitoring.nvidia import NvidiaMonitor
from monitoring.woodyminer_stats import WoodyminerStatsUploader, derive_machine_id
from monitoring import xenblockscan_reporter as xbs
from networking.difficulty import accept_network_difficulty
from networking.health import check_port80
from networking.poller import NetworkPoller
from networking.submit_result import (
    counts_as_reject,
    is_difficulty_mismatch,
    is_xuni_window_reject,
    submit_response_hint,
)
from networking.submitter import Submitter
from block_queue.flush import QueueFlusher
from block_queue.submit_cpu import SubmitCpuPool, submit_worker_count
from block_queue.policy import XUNI_WINDOW_LABEL, in_xuni_window
from block_queue.store import (
    DIFFICULTY_CHANGE_REASON,
    OUTSIDE_XUNI_WINDOW_REASON,
    SHUTDOWN_PENDING_REASON,
    BlockStore,
)
from strategies.registry import build_key_factory


class Supervisor:
    def __init__(self, settings: Settings, use_dashboard: bool = True) -> None:
        self.settings = settings
        self.use_dashboard = use_dashboard
        self.logger = SessionLogger(settings.log_path, echo_console=not use_dashboard)
        self.dashboard = MinerDashboard(settings) if use_dashboard else None
        self.local_stats = LocalMiningStatsTracker(
            settings.log_path.parent / "mining_stats_history.json"
        )
        self.wallet_balances: WalletBalanceTracker | None = None
        self.server_uptime: ServerUptimeTracker | None = None
        if self.dashboard:
            self.server_uptime = ServerUptimeTracker(
                settings.log_path.parent / "server_uptime.json",
            )
            self.dashboard.set_server_uptime(self.server_uptime)
        if self.dashboard and settings.address:
            self.wallet_balances = WalletBalanceTracker(
                settings.address,
                settings.log_path.parent / "balance_history.json",
            )
            self.dashboard.set_wallet_balances(self.wallet_balances)
        if self.dashboard:
            self.dashboard.set_local_stats(self.local_stats)
        self.lock = InstanceLock(settings.log_path.parent / "miner.lock")
        self.metrics = MetricsTracker()
        self.gpu = NvidiaMonitor(device_index=0, logger=self.logger if not use_dashboard else None)
        self.store = BlockStore(
            settings.db_path,
            settings.jsonl_path,
            settings.rejected_jsonl_path,
        )
        self.submitter = Submitter(
            settings.verify_url, settings.address, settings.worker, self.logger
        )
        self.submit_pool = SubmitCpuPool(settings.submit_cpu_fraction)
        self.flusher = QueueFlusher(
            self.store,
            self.submitter,
            self.logger,
            self.settings,
            self._mining_difficulty,
            metrics=self.metrics,
            local_stats=self.local_stats,
            submit_pool=self.submit_pool,
        )
        # Placeholders until total VRAM is known; _apply_vram_policy() resolves %.
        self.vram_caps: VramCaps | None = None
        self.budget = VramBudget(0, 0)
        self.guard = VramGuard(
            0,
            0,
            0,
            settings.min_headroom_floor_mib,
            settings.max_gpu_temp_c,
            settings.warn_gpu_temp_c,
            settings.gpu_cooldown_s,
        )
        self.is_cuda_native = settings.backend == "cuda"
        self.is_legacy_gpu = settings.backend == "gpu" or settings.gpu_enabled
        self.is_gpu = self.is_cuda_native or self.is_legacy_gpu
        self.backend = self._build_backend()
        self._apply_vram_policy()
        self.lanes = LaneManager(
            self.backend,
            self.budget,
            settings.cpu_lanes,
            settings.lane_ramp_step,
            self.logger,
            enabled=not self.is_gpu,
        )
        self.watcher = (
            XenblocksDbWatcher(settings.xenblocks_db)
            if self.is_legacy_gpu and settings.xenblocks_db
            else None
        )
        self._running = False
        self._shutting_down = False
        self._network_ok = False
        self._network_difficulty: int | None = None
        self._net_poller = NetworkPoller(
            settings.difficulty_url,
            poll_interval_s=float(settings.network_poll_interval_s),
            down_poll_interval_s=float(settings.network_down_poll_interval_s),
            timeout_s=float(settings.network_poll_timeout_s),
        )
        self._last_network_log_ok: bool | None = None
        self._power_booster: GpuPowerBooster | None = None
        if self.is_gpu and settings.gpu_power_boost_enabled:
            self._power_booster = GpuPowerBooster(
                self.gpu,
                target_pct=settings.gpu_power_target_pct,
                warn_temp_c=settings.warn_gpu_temp_c,
                max_temp_c=settings.max_gpu_temp_c,
                logger=self.logger if not use_dashboard else None,
                windows_performance_mode=settings.gpu_windows_performance_mode,
            )
        self._cooldown_until = 0.0
        self._last_gpu_warn_at = 0.0
        self._last_gpu_warn_code = ""
        self._reduce_lanes_after_cooldown = False
        self._defer_submit_until = 0.0
        self._temp_lane_log_path = settings.log_path.parent / "gpu_temp_lane.log"
        self.timelapse: SessionTimelapse | None = None
        self._session_started_at = 0.0
        self._woodyminer_uploader: WoodyminerStatsUploader | None = None
        if settings.woodyminer_enabled and settings.address:
            self._woodyminer_uploader = WoodyminerStatsUploader(
                upload_url=settings.woodyminer_upload_url,
                upload_period_s=settings.woodyminer_upload_period_s,
                custom_name=settings.woodyminer_custom_name,
                miner_address=settings.address,
                machine_id=derive_machine_id(device_index=0),
                get_stats=lambda: self.metrics.stats,
                get_gpu=self.gpu.snapshot,
                get_difficulty=self._mining_difficulty,
                session_started_at=0.0,
                logger=self.logger,
            )
        self._xbs_last_holdings_at = 0.0
        xbs.configure(
            enabled=settings.xenblockscan_enabled and bool(settings.address),
            endpoint=settings.xenblockscan_endpoint,
            api_key=settings.xenblockscan_api_key,
            report_rejects=settings.xenblockscan_report_rejects,
        )
        if settings.xenblockscan_enabled and settings.address:
            self._log(
                "info",
                f"XenBlockScan reporting ON → {settings.xenblockscan_endpoint}",
            )

    def _build_backend(self):
        if self.settings.backend == "cuda":
            return CudaNativeBackend(self.settings, self.settings.strategy)
        if self.is_gpu:
            self.logger.warn(
                "backend=gpu uses an external xenblocks binary — set backend=cpu "
                "or backend=cuda for the native miner"
            )
            return XenblocksGpuBackend(self.settings)
        key_factory = build_key_factory(self.settings.strategy)
        return CpuArgon2Backend(self.settings, key_factory, self.settings.strategy)

    def _resolve_total_vram_mib(self) -> int | None:
        snap = self.gpu.snapshot()
        if snap is not None and snap.total_mib > 0:
            return int(snap.total_mib)
        if self.is_cuda_native and isinstance(self.backend, CudaNativeBackend):
            try:
                info = self.backend._engine.device_info(0)
                total = int(info.total_vram_bytes) // (1024 * 1024)
                if total > 0:
                    return total
            except Exception:
                pass
        return None

    def _apply_vram_policy(self, total_mib: int | None = None) -> None:
        """Scale VRAM caps as % of this GPU's total memory."""
        total = total_mib if total_mib is not None else self._resolve_total_vram_mib()
        if total is None or total <= 0:
            return
        s = self.settings
        caps = resolve_vram_caps(
            total,
            target_pct=s.target_vram_pct,
            desktop_headroom_pct=s.desktop_headroom_pct,
            emergency_vram_pct=s.emergency_vram_pct,
            min_headroom_pct=s.min_headroom_pct,
            runtime_overhead_pct=s.runtime_overhead_pct,
            min_headroom_floor_mib=s.min_headroom_floor_mib,
            overhead_floor_mib=s.runtime_overhead_floor_mib,
            target_mib_override=s.target_vram_mib,
            headroom_mib_override=s.headroom_mib,
            emergency_mib_override=s.emergency_vram_mib,
            min_headroom_mib_override=s.min_headroom_mib,
            runtime_overhead_mib_override=s.cuda_runtime_overhead_mib,
        )
        if self.vram_caps is not None and self.vram_caps == caps:
            return
        self.vram_caps = caps
        self.budget = VramBudget(caps.target_mib, caps.headroom_mib)
        self.guard = VramGuard(
            caps.target_mib,
            caps.headroom_mib,
            caps.emergency_mib,
            caps.min_headroom_mib,
            s.max_gpu_temp_c,
            s.warn_gpu_temp_c,
            s.gpu_cooldown_s,
        )
        if isinstance(self.backend, CudaNativeBackend):
            self.backend.set_vram_caps(caps)
        self._log("info", caps.summary())

    def _kill_stray_xenblocks(self) -> None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/IM", "xenblocks.exe"],
                capture_output=True,
                timeout=10,
            )
            return
        # Linux / Unix: stop common binary names if a legacy bridge is in use.
        for name in ("xenblocks", "xenblocks_miner", "XenblocksMiner"):
            try:
                subprocess.run(
                    ["pkill", "-x", name],
                    capture_output=True,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _log(self, level: str, msg: str) -> None:
        getattr(self.logger, level)(msg)

    def _fail_startup(self, msg: str) -> bool:
        self._log("error", msg)
        print(f"ERROR: {msg}", flush=True)
        self.lock.release()
        return False

    def _difficulty_transition_settle_s(self) -> float:
        return max(5.0, float(self.settings.sample_interval_s))

    def _in_difficulty_transition(self) -> bool:
        return time.time() < self._defer_submit_until

    def _begin_difficulty_transition(self, old_diff: int, new_diff: int) -> None:
        settle_s = self._difficulty_transition_settle_s()
        self._defer_submit_until = time.time() + settle_s
        self._log(
            "info",
            f"Difficulty {old_diff} -> {new_diff} — lane replan; "
            f"queuing new hits for {settle_s:.0f}s (no live submit/flush)",
        )
        if self.dashboard:
            self.dashboard.set_status("Lane replan — queuing hits...")

    def _maybe_restore_cuda_lane_cap(self, difficulty: int) -> None:
        if not self.is_cuda_native or not isinstance(self.backend, CudaNativeBackend):
            return
        ref = self.settings.vram_reference_difficulty
        if difficulty < ref:
            return
        snap = self.gpu.snapshot()
        if snap is None:
            return
        before = self.backend.max_lanes_cap
        if not self.backend.restore_lane_cap_if_cool(
            snap.temperature_c,
            difficulty=difficulty,
        ):
            return
        after = self.backend.max_lanes_cap
        self._log(
            "info",
            f"Lane cap restored {before} -> {after} at difficulty {difficulty} "
            f"(temp {snap.temperature_c}C) — ready for next harvest push",
        )

    def _log_cuda_harvest_push(self, difficulty: int) -> None:
        if not self.is_cuda_native or not isinstance(self.backend, CudaNativeBackend):
            return
        plan = getattr(self.backend, "vram_plan", None)
        if plan is None:
            return
        cap_note = ""
        if self.backend.max_lanes_cap < self.settings.cuda_max_lanes:
            cap_note = (
                f" lane_cap={self.backend.max_lanes_cap}/"
                f"{self.settings.cuda_max_lanes}"
            )
        mode = getattr(self.backend, "parallel_mode", "sequential")
        self._log(
            "info",
            f"Harvest push difficulty={difficulty}: {plan.summary()} "
            f"parallel={mode}{cap_note}",
        )

    def _apply_network_difficulty(self, raw_diff: int) -> int:
        fallback = self._network_difficulty or self.settings.memory_cost
        diff = accept_network_difficulty(raw_diff, fallback=fallback)
        if raw_diff <= 0:
            self._log("warn", f"Invalid network difficulty {raw_diff}; using {diff}")
        elif self._network_difficulty is not None and diff != self._network_difficulty:
            old_diff = self._network_difficulty
            ref = self.settings.vram_reference_difficulty
            if diff >= ref and old_diff < ref:
                self._maybe_restore_cuda_lane_cap(diff)
            self._begin_difficulty_transition(old_diff, diff)
        return diff

    def _sync_dashboard_stats(self, snap=None) -> None:
        if not self.dashboard:
            return
        if snap is None and self.gpu.available:
            snap = self.gpu.snapshot()
        self.dashboard.update(
            self.metrics.stats,
            snap,
            pending_by_type=self.store.pending_by_type(resubmission=False),
            resubmission_by_type=self.store.pending_by_type(resubmission=True),
        )

    def _hydrate_accepted_metrics_from_local_stats(self) -> None:
        counts = self.local_stats.today_counts()
        stats = self.metrics.stats
        stats.accepted_live_xuni = counts["XUNI"]
        stats.accepted_live_xnm = counts["XNM"]
        stats.accepted_live_xblk = counts["XBLK"]
        total = sum(counts.values())
        if total:
            parts = [
                f"{kind}={counts[kind]}"
                for kind in ("XUNI", "XNM", "XBLK")
                if counts[kind]
            ]
            self._log("info", f"Restored today's accepted counts: {', '.join(parts)}")

    def _ui_event(self, action: str, block: str, detail: str = "") -> None:
        if self.timelapse:
            label = f"{action} {block}"
            if detail:
                label = f"{label} {detail}"
            self.timelapse.record_event(label)
        if self.dashboard:
            self.dashboard.event(action, block, detail)
            self._sync_dashboard_stats()

    def _resume_queued_blocks(self) -> None:
        """Submit resumed XNM/XBLK immediately; XUNI when window allows."""
        counts = self.store.pending_by_type()
        total = sum(counts.values())
        if not total:
            return

        parts = [f"{kind}={counts[kind]}" for kind in ("XUNI", "XNM", "XBLK") if counts[kind]]
        self._log(
            "info",
            f"Resuming with {total} queued block(s) from previous session ({', '.join(parts)})",
        )

        xuni_waiting = counts["XUNI"] if counts["XUNI"] and not in_xuni_window() else 0
        xuni_ready = counts["XUNI"] - xuni_waiting
        submit_now = counts["XNM"] + counts["XBLK"] + xuni_ready

        if not self._network_ok:
            self._log("info", "Queued blocks held until network is available")
            if xuni_waiting:
                self._log(
                    "info",
                    f"{xuni_waiting} XUNI block(s) will submit in next window "
                    f"({XUNI_WINDOW_LABEL})",
                )
            return

        if submit_now:
            self._log("info", f"Submitting {submit_now} eligible queued block(s)...")
            if self.dashboard:
                self.dashboard.set_status(f"Submitting {submit_now} queued block(s)...")
                self._ui_refresh()
            flushed = self.flusher.flush()
            self.metrics.sync_pending(self.store.pending_count())
            if flushed:
                self._log("info", f"Submitted {flushed} queued block(s) on startup")
                self._ui_event("ACCEPTED", "QUEUE", f"startup flushed {flushed}")
        elif xuni_waiting:
            self._log(
                "info",
                f"{xuni_waiting} XUNI block(s) held until window ({XUNI_WINDOW_LABEL})",
            )

        remaining = self.store.pending_count()
        if remaining:
            self._log("info", f"{remaining} block(s) still pending in queue")

    def _ui_refresh(self, snap=None) -> None:
        if self.timelapse:
            self.timelapse.maybe_sample(
                self.metrics.stats,
                snap,
                pending=self.store.pending_count(),
                network_ok=self._network_ok,
            )
        if self.dashboard:
            if self.wallet_balances is not None:
                self.wallet_balances.maybe_daily_refresh()
                self.wallet_balances.maybe_refresh()
            if self.is_cuda_native and hasattr(self.backend, "batch_size"):
                self.dashboard.set_cuda_batch(
                    self.backend.batch_size,
                    getattr(self.backend, "active_lanes", 1),
                )
            self.dashboard.update(
                self.metrics.stats,
                snap,
                pending_by_type=self.store.pending_by_type(resubmission=False),
                resubmission_by_type=self.store.pending_by_type(resubmission=True),
            )
        self._maybe_report_xbs_holdings()

    def _gpu_temp_abort_check(self) -> bool:
        snap = self.gpu.snapshot()
        if snap is None:
            return False
        return snap.temperature_c >= self.settings.max_gpu_temp_c

    def _gpu_paused_for_cooldown(self) -> bool:
        return self.is_gpu and time.time() < self._cooldown_until

    def _gpu_backend_running(self) -> bool:
        if not self.is_gpu:
            return True
        is_running = getattr(self.backend, "is_running", None)
        if is_running is None:
            return True
        return bool(is_running)

    def _note_temp_cooldown(self, reason: str, snap) -> None:
        """Remember why we cooled down; trim lanes on restart if multi-lane harvest."""
        if not self.is_cuda_native or snap is None:
            return
        lanes = int(getattr(self.backend, "active_lanes", 1))
        diff = self._mining_difficulty()
        temp = snap.temperature_c
        if lanes > 1:
            self._reduce_lanes_after_cooldown = True
            append_lane_event(
                self._temp_lane_log_path,
                (
                    f"TEMP COOLDOWN difficulty={diff} temp={temp}C lanes={lanes} "
                    f"| low-difficulty multi-lane harvest likely raised heat "
                    f"| {reason}"
                ),
            )
            self._log(
                "warn",
                f"GPU temp {temp}C with {lanes} lanes at difficulty {diff} — "
                "will reduce lane cap by 1 when GPU restarts after cooldown",
            )
        else:
            append_lane_event(
                self._temp_lane_log_path,
                (
                    f"TEMP COOLDOWN difficulty={diff} temp={temp}C lanes=1 "
                    f"| reference single-lane load — no lane reduction "
                    f"| {reason}"
                ),
            )
            self._log(
                "info",
                f"GPU temp {temp}C at difficulty {diff} with 1 lane — "
                "cooldown only, lane cap unchanged",
            )

    def _apply_lane_cap_after_temp(self, snap) -> None:
        if not self._reduce_lanes_after_cooldown:
            return
        if not isinstance(self.backend, CudaNativeBackend):
            self._reduce_lanes_after_cooldown = False
            return
        lanes_active = int(self.backend.active_lanes)
        if lanes_active <= 1:
            self._reduce_lanes_after_cooldown = False
            return
        before = self.backend.max_lanes_cap
        after = self.backend.reduce_lane_cap()
        self._reduce_lanes_after_cooldown = False
        if after >= before:
            return
        temp = snap.temperature_c if snap is not None else 0
        diff = self._mining_difficulty()
        policy = record_temp_lane_reduction(
            self.backend.lane_policy_path,
            self._temp_lane_log_path,
            self.backend.lane_policy,
            temperature_c=temp,
            difficulty=diff,
            lanes_active=lanes_active,
            lanes_before=before,
            lanes_after=after,
            reason="GPU temp cap hit during multi-lane low-difficulty mining",
        )
        self.backend._lane_policy = policy
        self._log(
            "warn",
            f"Lane cap reduced {before} -> {after} after temp cooldown "
            f"(difficulty={diff}, lanes_active={lanes_active}) — "
            f"see {self._temp_lane_log_path.name}",
        )
        if self.dashboard:
            self._ui_event("WARN", "GPU", f"lanes cap {before}->{after}")

    def _try_flush_pending_queue(self, *, context: str) -> int:
        """Submit queued blocks when the network is up; otherwise leave them queued."""
        pending = self.store.pending_count()
        if not pending:
            return 0
        if self._in_difficulty_transition():
            self._log(
                "info",
                f"{pending} queued block(s) held ({context} — difficulty transition)",
            )
            return 0
        if not self.refresh_network():
            self._log(
                "info",
                f"{pending} queued block(s) held ({context} — network down)",
            )
            return 0
        flushed = self.flusher.flush()
        self.metrics.sync_pending(self.store.pending_count())
        if flushed:
            self._log("info", f"Submitted {flushed} queued block(s) ({context})")
            self._ui_event("ACCEPTED", "QUEUE", f"{context} flushed {flushed}")
        return flushed

    def _service_pending_queue(
        self,
        now: float,
        last_queue_flush: float,
        last_xuni_flush: float,
        xuni_flush_interval_s: float,
        *,
        aggressive: bool = False,
    ) -> tuple[float, float]:
        """Keep flushing queued blocks on schedule while GPU mining is paused."""
        in_window = in_xuni_window()
        pending_xuni = self.store.pending_by_type()["XUNI"]
        flush_interval_s = 5.0 if aggressive else 30.0

        if in_window and not self._was_in_xuni_window:
            self._was_in_xuni_window = True
            if pending_xuni:
                self._log(
                    "info",
                    f"XUNI window open ({XUNI_WINDOW_LABEL}) — "
                    f"flushing {pending_xuni} queued block(s)",
                )
                self._try_flush_pending_queue(context="XUNI window")
                last_xuni_flush = now
                last_queue_flush = now
        elif not in_window:
            self._was_in_xuni_window = False

        if aggressive or now - last_queue_flush >= flush_interval_s:
            context = "GPU cooldown" if aggressive else "queue service"
            self._try_flush_pending_queue(context=context)
            last_queue_flush = now
        elif (
            in_window
            and pending_xuni
            and now - last_xuni_flush >= xuni_flush_interval_s
        ):
            self._try_flush_pending_queue(context="XUNI interval")
            last_xuni_flush = now

        return last_queue_flush, last_xuni_flush

    def _graceful_gpu_stop(self, reason: str, *, cooldown_s: int, snap=None) -> None:
        """Stop the GPU backend cleanly and pause mining until cooldown elapses."""
        self._note_temp_cooldown(reason, snap)
        self._log("warn", reason)
        self._ui_event("WARN", "GPU", "Cooling down...")
        if self.dashboard:
            self.dashboard.set_status("GPU cooling down...")
        self._try_flush_pending_queue(context="GPU cooldown")
        try:
            self.backend.stop()
        except Exception as exc:
            self._log("warn", f"Error stopping GPU backend: {exc}")
        self._cooldown_until = time.time() + cooldown_s

    def _graceful_shutdown(self, reason: str) -> None:
        """Stop mining immediately, then try to send any queued blocks."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self._running = False
        self._log("info", reason)
        if self.dashboard:
            self.dashboard.set_status("Stopping mining...")

        try:
            self.backend.stop()
        except Exception as exc:
            self._log("warn", f"Error stopping miner backend: {exc}")

        pending_by_type = self.store.pending_by_type()
        submit_now = pending_by_type["XNM"] + pending_by_type["XBLK"]
        xuni_pending = pending_by_type["XUNI"]
        xuni_held = xuni_pending if xuni_pending and not in_xuni_window() else 0
        xuni_to_send = xuni_pending - xuni_held
        sending = submit_now + xuni_to_send

        if sending or xuni_held:
            network_up = False
            if sending:
                if self._network_ok:
                    network_up = self.refresh_network(blocking=True)
                else:
                    self._log(
                        "info",
                        "Network down on shutdown — skipping submit attempts",
                    )
            if sending and xuni_held:
                msg = (
                    f"Sending {sending} queued block(s) before exit "
                    f"({xuni_held} XUNI held for next start)..."
                    if network_up
                    else (
                        f"Queuing {sending} block(s) for next start "
                        f"({xuni_held} XUNI outside window)..."
                    )
                )
            elif sending:
                msg = (
                    f"Sending {sending} queued block(s) before exit..."
                    if network_up
                    else f"Queuing {sending} block(s) for next start (network down)..."
                )
            else:
                msg = (
                    f"Holding {xuni_held} queued XUNI block(s) for next start "
                    f"(outside window)..."
                )
            self._log("info", msg)
            if self.dashboard:
                self.dashboard.set_status(msg)
                self._ui_refresh(self.gpu.snapshot())
            if sending:
                try:
                    if network_up:
                        flushed = self.flusher.flush(on_shutdown=True)
                        if flushed:
                            self._log(
                                "info",
                                f"Sent {flushed} queued block(s) on shutdown",
                            )
                    else:
                        self.flusher.defer_to_next_start()
                        flushed = 0
                except Exception as exc:
                    self._log("warn", f"Shutdown flush error: {exc}")
                    flushed = 0
                self.metrics.sync_pending(self.store.pending_count())
            remaining_by_type = self.store.pending_by_type()
            remaining = sum(remaining_by_type.values())
            if remaining:
                parts = [
                    f"{kind}={remaining_by_type[kind]}"
                    for kind in ("XUNI", "XNM", "XBLK")
                    if remaining_by_type[kind]
                ]
                self._log(
                    "info",
                    f"Queued for next start: {', '.join(parts)}",
                )
            if self.dashboard:
                self._ui_refresh(self.gpu.snapshot())
        elif self.dashboard:
            self.dashboard.set_status("Shutting down...")

    def _finalize_session(self) -> None:
        if self._woodyminer_uploader is not None:
            self._woodyminer_uploader.stop()
        if self.timelapse:
            self.timelapse.finalize()
        try:
            self.backend.stop()
        except Exception:
            pass
        self.submit_pool.shutdown(wait=False)
        if self._power_booster is not None:
            self._power_booster.restore()
        self._net_poller.stop()
        self.gpu.shutdown()
        self.lock.release()
        if self.dashboard:
            self.dashboard.set_status("Stopped")
            self._ui_refresh(self.gpu.snapshot())
            self.dashboard.stop()
        self._log("info", "=== XenBlocks Miner by Tony.x1 STOPPED ===")

    def startup_checks(self) -> bool:
        if not self.lock.acquire():
            return self._fail_startup(
                "Another miner instance is already running (miner.lock). "
                "Close the other miner or delete data/miner.lock if it is stale."
            )

        diag = run_diagnostics(self.settings)
        self._log("info", f"Diagnostics: {diag}")
        if not diag["calibration_m100"]:
            return self._fail_startup(
                "Argon2 calibration failed - check wallet salt / key format"
            )

        if self.is_cuda_native:
            self._kill_stray_xenblocks()
            self._apply_vram_policy()
            gpu = diag.get("gpu") or {}
            used = int(gpu.get("used_mib", 0))
            total = int(gpu.get("total_mib", 0)) or (
                self.vram_caps.total_mib if self.vram_caps else 0
            )
            target = self.vram_caps.target_mib if self.vram_caps else 0
            # Another miner if used already exceeds our target by a large margin.
            slack = max(2048, int(total * 0.05)) if total else 2048
            if target > 0 and used > target + slack:
                return self._fail_startup(
                    f"GPU already using {used}MiB VRAM - another miner may be active. "
                    f"Stop other GPU miners first (target is {target}MiB / "
                    f"{self.settings.target_vram_pct:.0f}% of {total}MiB)."
                )
            self._log(
                "info",
                "CUDA native mode: libxen_cuda - VRAM/temp watchdog active",
            )
        elif self.is_legacy_gpu:
            self._kill_stray_xenblocks()
            self._log("info", "Legacy GPU mode: external xenblocks binary supervised")
        return True

    def refresh_network(self, *, blocking: bool = False) -> bool:
        if blocking:
            status = self._net_poller.poll_once(
                timeout_s=float(self.settings.connection_timeout_s),
            )
        else:
            status = self._net_poller.get_status()
        if status.difficulty is not None:
            raw_diff = int(status.difficulty)
            if self._network_difficulty != raw_diff:
                diff = self._apply_network_difficulty(raw_diff)
                self._network_difficulty = diff
                if self.is_cuda_native and hasattr(self.backend, "set_difficulty"):
                    self.backend.set_difficulty(diff)
                    plan = getattr(self.backend, "vram_plan", None)
                    if plan is not None:
                        ref = self.settings.vram_reference_difficulty
                        if diff < ref:
                            self._log_cuda_harvest_push(diff)
                        else:
                            self._log("info", f"CUDA VRAM plan: {plan.summary()}")
                        if self.dashboard:
                            self.dashboard.set_cuda_batch(
                                self.backend.batch_size,
                                self.backend.active_lanes,
                            )
            self._network_ok = True
            if self.dashboard:
                self.dashboard.set_network(True, self._network_difficulty)
            if self.server_uptime is not None:
                self.server_uptime.record_probe(True)
            was_down = self._last_network_log_ok is False
            self._last_network_log_ok = True
            if was_down:
                self._log("info", "Network back online")
                if self.store.pending_count() > 0:
                    self._try_flush_pending_queue(context="network recovery")
            return True
        self._network_ok = False
        if self._last_network_log_ok is not False:
            self._log("warn", f"Network down: {status.error}")
        self._last_network_log_ok = False
        if self.dashboard:
            self.dashboard.set_network(False, self._network_difficulty)
        if self.server_uptime is not None:
            self.server_uptime.record_probe(False)
        return False

    def _mining_difficulty(self) -> int:
        if self.is_cuda_native and hasattr(self.backend, "difficulty"):
            return int(self.backend.difficulty)
        return self._network_difficulty or self.settings.memory_cost

    def _report_xbs_accept(self, hit: BlockHit, kind: str) -> None:
        """Share live/flush accept with XenBlockScan (XUNI/XBLK/XNM)."""
        if not self.settings.xenblockscan_enabled or not self.settings.address:
            return
        try:
            xbs.report_accepted(
                account=self.settings.address,
                kind=kind,
                key=hit.key or "",
                hash_to_verify=hit.hash_str or "",
                worker=self.settings.worker or "",
                difficulty=hit.memory_cost
                if hit.memory_cost is not None
                else self._mining_difficulty(),
                occurred_at=getattr(hit, "found_at", None),
            )
        except Exception as exc:
            self._log("warn", f"xenblockscan accept report failed: {exc}")

    def _maybe_report_xbs_holdings(self, *, force: bool = False) -> None:
        if not self.settings.xenblockscan_enabled or not self.settings.address:
            return
        # Hashrate needs fresher samples than balances — default 30s
        interval = max(15, int(self.settings.xenblockscan_holdings_interval_s or 30))
        now = time.time()
        if not force and now - self._xbs_last_holdings_at < interval:
            return
        self._xbs_last_holdings_at = now
        xnm = xuni = xblk = None
        if self.wallet_balances is not None:
            try:
                view = self.wallet_balances.view()
                cur = view.current
                if cur is not None:
                    xnm, xuni, xblk = cur.xnm, cur.xuni, cur.xblk
            except Exception:
                pass
        # Live hashrate from this miner (H/s EMA) — site source of truth
        hps = None
        try:
            hps = float(self.metrics.stats.hps_ema or 0.0)
            if hps <= 0:
                hps = None
        except (TypeError, ValueError, AttributeError):
            hps = None
        s = self.metrics.stats
        accepted = int(getattr(s, "accepted_total", 0) or 0)
        rejected = int(getattr(s, "rejected_total", 0) or 0)
        found = int(getattr(s, "found_total", 0) or 0)
        tid = (self.settings.tracker_id or "").strip()
        try:
            xbs.report_holdings(
                account=self.settings.address,
                worker=self.settings.worker or "",
                xnm=xnm,
                xuni=xuni,
                xblk=xblk,
                hashrate=hps,
                tracker_id=tid,
            )
        except Exception as exc:
            self._log("warn", f"xenblockscan holdings report failed: {exc}")
        # Always heartbeat the fleet tracker (even while hashrate warms up)
        try:
            if tid:
                xbs.report_tracker(
                    tracker_id=tid,
                    account=self.settings.address,
                    worker=self.settings.worker or "",
                    hashrate=hps,
                    accepted=accepted,
                    rejected=rejected,
                    found=found,
                    difficulty=self._network_difficulty,
                    network_ok=bool(self._network_ok),
                )
        except Exception as exc:
            self._log("warn", f"xenblockscan tracker heartbeat failed: {exc}")

    def _schedule_xbs_backfill(self) -> None:
        """Optional deferred history feed — never on the startup/mining path."""
        if (
            not self.settings.xenblockscan_enabled
            or not self.settings.xenblockscan_backfill
            or not self.settings.address
        ):
            return
        try:
            # Tiny recent sample only; site already has bulk data if needed
            rows = self.store.list_accepted_submits(limit=300)
            if not rows:
                return
            xbs.schedule_backfill(
                rows,
                account=self.settings.address,
                worker=self.settings.worker or "",
                max_rows=100,
                delay_s=60.0,
            )
            self._log(
                "info",
                "XenBlockScan history feed scheduled in background "
                "(does not block mining)",
            )
        except Exception as exc:
            self._log("warn", f"xenblockscan backfill schedule failed: {exc}")

    def _prepare_live_hit(self, hit: BlockHit) -> BlockHit | None:
        return prepare_hit_for_submit(
            hit,
            salt_hex=self.settings.salt_hex,
            memory_cost=self._mining_difficulty(),
            time_cost=self.settings.time_cost,
            parallelism=self.submit_pool.parallelism_for_single(
                self.settings.parallelism
            ),
            hash_len=self.settings.hash_len,
        )

    def _attach_hit_memory_cost(self, hit: BlockHit) -> BlockHit:
        if hit.memory_cost is not None:
            return hit
        if self.is_cuda_native and hasattr(self.backend, "difficulty"):
            return BlockHit(
                key=hit.key,
                hash_str=hit.hash_str,
                block_type=hit.block_type,
                attempts=hit.attempts,
                strategy=hit.strategy,
                hps=hit.hps,
                found_at=hit.found_at,
                memory_cost=int(self.backend.difficulty),
            )
        return BlockHit(
            key=hit.key,
            hash_str=hit.hash_str,
            block_type=hit.block_type,
            attempts=hit.attempts,
            strategy=hit.strategy,
            hps=hit.hps,
            found_at=hit.found_at,
            memory_cost=self._mining_difficulty(),
        )

    def handle_hit(self, hit: BlockHit) -> None:
        hit = self._attach_hit_memory_cost(hit)
        kind = classify_block(hit.hash_str, hit.block_type)
        if kind == "OTHER":
            kind = hit.block_type or "OTHER"

        self.metrics.record_found(kind)
        self._log(
            "info",
            f"HIT {kind} strategy={hit.strategy} key={hit.key[:16]}...",
        )
        self._ui_event("FOUND", kind, hit.strategy)

        if self._in_difficulty_transition():
            self._queue_hit(
                hit,
                kind,
                DIFFICULTY_CHANGE_REASON,
                "mining stable",
            )
            return

        prepared = self.submit_pool.run(self._prepare_live_hit, hit)
        if prepared is None:
            self._log(
                "info",
                f"Filtered false positive key={hit.key[:16]}... "
                f"(CUDA base64 match did not verify on CPU)",
            )
            return

        hit = prepared

        if self._shutting_down:
            if kind == "XUNI" and not in_xuni_window():
                self._queue_hit(
                    hit, kind, OUTSIDE_XUNI_WINDOW_REASON, "next XUNI window"
                )
            else:
                self._queue_hit(hit, kind, SHUTDOWN_PENDING_REASON, "next start")
            return

        if kind == "XUNI" and not in_xuni_window():
            self._queue_hit(hit, kind, OUTSIDE_XUNI_WINDOW_REASON, "next XUNI window")
            return

        if not self.refresh_network():
            self._queue_hit(hit, kind, "network_down", "network back")
            return

        result = self.submitter.submit(hit)
        if result["ok"]:
            self.metrics.record_accepted_live(kind)
            self.local_stats.record_accept(kind)
            hint = submit_response_hint(result["status"], result["body"])
            self._ui_event("ACCEPTED", kind, hint)
            self._log("info", f"SUBMIT OK {kind} {hint}")
            self.store.record_direct_submit(hit, result["status"], result["body"])
            self._report_xbs_accept(hit, kind)
            return

        status = int(result["status"])
        body = str(result.get("body") or "")

        if status == 0 or not self._network_ok:
            self.store.record_direct_submit(hit, status, body)
            self._queue_hit(hit, kind, "network_down", "network back")
            return

        # Difficulty / XUNI-window responses are not permanent rejects — hold
        # the block until conditions match, without inflating reject counters
        # or woodyminer rejectedBlocks.
        if is_difficulty_mismatch(status, body):
            self._queue_hit(
                hit, kind, DIFFICULTY_CHANGE_REASON, "difficulty matches"
            )
            self._log(
                "info",
                f"Hold {kind} key={hit.key[:16]}... until difficulty matches "
                f"({submit_response_hint(status, body)})",
            )
            return

        if is_xuni_window_reject(status, body):
            self._queue_hit(
                hit, kind, OUTSIDE_XUNI_WINDOW_REASON, "next XUNI window"
            )
            self._log(
                "info",
                f"Hold {kind} key={hit.key[:16]}... until XUNI window "
                f"({submit_response_hint(status, body)})",
            )
            return

        newly_queued = self.store.record_rejection(hit, status, body, "live")
        if counts_as_reject(status, body):
            self.metrics.record_rejected_live(kind, hit.key)
        if newly_queued:
            self.metrics.record_resubmission(kind)
            self.metrics.sync_pending(self.store.pending_count())
        self._ui_event("RESUBMIT", kind, f"HTTP {status}")
        hint = submit_response_hint(status, body)
        self._log(
            "warn",
            f"Live submit failed {kind} key={hit.key[:16]}... ({hint}) — queued for retry",
        )

    def _queue_hit(self, hit: BlockHit, kind: str, reason: str, retry_when: str) -> None:
        self.store.enqueue(hit, reason=reason)
        self.metrics.record_enqueued(kind)
        self.metrics.sync_pending(self.store.pending_count())
        self._ui_event("QUEUED", kind, retry_when)
        self._log("info", f"QUEUED {kind} ({reason}) — will retry when {retry_when}")

    def _apply_gpu_safety(self, snap) -> bool:
        action = self.guard.evaluate(snap)
        if action.level == "warn":
            now = time.time()
            if (
                action.code != self._last_gpu_warn_code
                or now - self._last_gpu_warn_at >= 60.0
            ):
                self._log("warn", action.message)
                self._ui_event("WARN", "GPU", action.message[:40])
                self._last_gpu_warn_code = action.code
                self._last_gpu_warn_at = now
            return True
        if action.level == "emergency":
            if action.graceful_stop:
                if self._gpu_paused_for_cooldown() and not self._gpu_backend_running():
                    return True
                self._graceful_gpu_stop(
                    f"GPU temp limit — {action.message}",
                    cooldown_s=action.cooldown_s,
                    snap=snap,
                )
                return True
            self._log("error", f"SAFETY STOP: {action.message}")
            self._ui_event("WARN", "GPU", "SAFETY STOP")
            if isinstance(self.backend, (XenblocksGpuBackend, CudaNativeBackend)):
                self.backend.stop()
            self._try_flush_pending_queue(context="GPU safety stop")
            self._cooldown_until = time.time() + action.cooldown_s
            return False
        return True

    def _maybe_restart_gpu(self) -> None:
        if time.time() < self._cooldown_until:
            return
        snap = self.gpu.snapshot()
        if isinstance(self.backend, XenblocksGpuBackend) and not self.backend.is_running:
            self._log("info", "Restarting xenblocks binary after cooldown")
            self.backend.start()
        elif isinstance(self.backend, CudaNativeBackend) and not self.backend.is_running:
            self._apply_lane_cap_after_temp(snap)
            self._log("info", "Restarting CUDA engine after cooldown")
            self.backend.start()
            if self.dashboard and hasattr(self.backend, "batch_size"):
                self.dashboard.set_cuda_batch(
                    self.backend.batch_size,
                    self.backend.active_lanes,
                )

    def run(self, max_seconds: int | None = None, skip_startup_checks: bool = False) -> None:
        if not skip_startup_checks and not self.startup_checks():
            return

        imported = self.store.import_pending_from_jsonl()
        if imported:
            self._log(
                "info",
                f"Imported {imported} queued block(s) from queue.jsonl into database",
            )

        migrated = self.store.migrate_rejected_pending()
        if migrated:
            self._log(
                "info",
                f"Moved {migrated} previously rejected block(s) to resubmission queue",
            )

        reclassified = self.store.reclassify_pending()
        if reclassified:
            self._log(
                "info",
                f"Reclassified {reclassified} queued block(s) to match official block rules",
            )

        self.timelapse = SessionTimelapse(
            self.settings.timelapse_path,
            sample_interval_s=self.settings.timelapse_sample_s,
        )
        if imported:
            self.timelapse.record_event(f"IMPORT {imported} queued")

        pending = self.store.pending_count()
        self.metrics.stats.queued = pending
        self._hydrate_accepted_metrics_from_local_stats()
        # XenBlockScan: never block startup. Live accepts + periodic holdings only.
        # Optional tiny history feed is deferred ~60s on a daemon thread.
        self._schedule_xbs_backfill()
        self._maybe_report_xbs_holdings(force=True)

        if self.dashboard:
            self.dashboard.start()
            self.dashboard.set_timelapse(self.timelapse)
            if self.wallet_balances is not None:
                self.wallet_balances.refresh_on_launch()
            self.dashboard.set_status("Starting...")
            self.dashboard.update(
                self.metrics.stats,
                None,
                pending_by_type=self.store.pending_by_type(resubmission=False),
                resubmission_by_type=self.store.pending_by_type(resubmission=True),
            )

        submit_workers = submit_worker_count(self.settings.submit_cpu_fraction)
        self._log(
            "info",
            f"Submit CPU budget: {submit_workers} core(s) "
            f"({int(self.settings.submit_cpu_fraction * 100)}% of "
            f"{os.cpu_count() or 1} available)",
        )
        self._log("info", "=== XenBlocks Miner by Tony.x1 START ===")
        if self.dashboard:
            self.dashboard.set_status("Connecting to server...")

        net = self._net_poller.start(
            initial_timeout_s=float(self.settings.connection_timeout_s),
        )
        if net.difficulty is not None:
            self._network_difficulty = self._apply_network_difficulty(net.difficulty)
            self._network_ok = True
            self._last_network_log_ok = True
            if self._network_difficulty == net.difficulty:
                self._log("info", f"Network difficulty: {self._network_difficulty}")
            if self.is_cuda_native and hasattr(self.backend, "set_difficulty"):
                self.backend.set_difficulty(self._network_difficulty)
            if self.dashboard:
                self.dashboard.set_network(True, self._network_difficulty)
        else:
            self._network_difficulty = self.settings.memory_cost
            self._network_ok = False
            self._last_network_log_ok = False
            if self.is_cuda_native and hasattr(self.backend, "set_difficulty"):
                self.backend.set_difficulty(self._network_difficulty)
            port_ok = check_port80(self.settings.base_url, timeout_s=3.0)
            self._log(
                "warn",
                f"Server unreachable ({net.error or 'no response'}) - "
                f"mining at configured difficulty {self._network_difficulty}",
            )
            if self.dashboard:
                self.dashboard.set_network(port_ok, self._network_difficulty)
        if self.server_uptime is not None:
            self.server_uptime.record_probe(self._network_ok)

        self._resume_queued_blocks()
        self.metrics.sync_pending(self.store.pending_count())
        if self.dashboard:
            self._ui_refresh()

        if self.is_cuda_native and hasattr(self.backend, "set_abort_check"):
            self.backend.set_abort_check(self._gpu_temp_abort_check)

        if self._power_booster is not None:
            self._power_booster.apply()

        if time.time() >= self._cooldown_until:
            self.backend.start()
            if self.is_cuda_native and hasattr(self.backend, "batch_size"):
                plan = getattr(self.backend, "vram_plan", None)
                if plan is not None:
                    self._log("info", f"CUDA VRAM plan: {plan.summary()}")
                else:
                    self._log(
                        "info",
                        f"CUDA batch={self.backend.batch_size:,} "
                        f"target_vram="
                        f"{(self.vram_caps.target_mib if self.vram_caps else 0):,}MiB "
                        f"desktop_headroom="
                        f"{(self.vram_caps.headroom_mib if self.vram_caps else 0):,}MiB",
                    )
                if self.dashboard:
                    self.dashboard.set_cuda_batch(
                    self.backend.batch_size,
                    self.backend.active_lanes,
                )

        if self.dashboard:
            self.dashboard.set_status("Mining")

        self._running = True
        started = time.time()
        self._session_started_at = started
        if self._woodyminer_uploader is not None:
            self._woodyminer_uploader.session_started_at = started
            self._woodyminer_uploader.start()
        now = started
        last_ui = 0.0
        last_net_check = now
        last_queue_flush = 0.0
        last_xuni_flush = 0.0
        last_tune = 0.0
        last_gpu_poll = 0.0
        last_power_tune = 0.0
        self._was_in_xuni_window = in_xuni_window()
        xuni_flush_interval_s = min(5.0, float(self.settings.stats_interval_s))

        try:
            while self._running:
                if max_seconds and time.time() - started >= max_seconds:
                    self._graceful_shutdown("Max runtime reached")
                    break

                snap = self.gpu.snapshot()
                now = time.time()

                if now - last_net_check >= float(self.settings.network_poll_interval_s):
                    self.refresh_network()
                    last_net_check = now

                if (
                    self._power_booster is not None
                    and now - last_power_tune >= self.settings.sample_interval_s
                ):
                    self._power_booster.adjust(snap)
                    last_power_tune = now

                if self.is_gpu:
                    if not self._apply_gpu_safety(snap):
                        last_queue_flush, last_xuni_flush = self._service_pending_queue(
                            now,
                            last_queue_flush,
                            last_xuni_flush,
                            xuni_flush_interval_s,
                            aggressive=True,
                        )
                        time.sleep(2)
                        self._ui_refresh(snap)
                        self._maybe_restart_gpu()
                        continue
                    if self.is_legacy_gpu or self.is_cuda_native:
                        self._maybe_restart_gpu()

                    if self._gpu_paused_for_cooldown() and not self._gpu_backend_running():
                        last_queue_flush, last_xuni_flush = self._service_pending_queue(
                            now,
                            last_queue_flush,
                            last_xuni_flush,
                            xuni_flush_interval_s,
                            aggressive=True,
                        )
                        time.sleep(2)
                        self._ui_refresh(snap)
                        self._maybe_restart_gpu()
                        continue

                    if now - last_gpu_poll >= 3.0 and self.watcher:
                        for hit in self.watcher.poll_new_hits():
                            self.handle_hit(hit)
                        last_gpu_poll = now

                if not self.is_gpu and now - last_tune >= self.settings.sample_interval_s:
                    self.lanes.tune(snap)
                    last_tune = now

                batch = self.backend.mine_batch(batch_size=1000)
                self.metrics.record_hashes(batch.hashes_done, self.backend.active_lanes)

                if batch.aborted:
                    self._graceful_gpu_stop(
                        batch.abort_reason or "GPU batch aborted at temp limit",
                        cooldown_s=self.settings.gpu_cooldown_s,
                        snap=snap,
                    )
                    last_queue_flush, last_xuni_flush = self._service_pending_queue(
                        time.time(),
                        last_queue_flush,
                        last_xuni_flush,
                        xuni_flush_interval_s,
                        aggressive=True,
                    )
                    self._ui_refresh(snap)
                    time.sleep(2)
                    self._maybe_restart_gpu()
                    continue

                if batch.hit:
                    self.handle_hit(batch.hit)
                    if isinstance(self.backend, CpuArgon2Backend):
                        self.backend.start()

                last_queue_flush, last_xuni_flush = self._service_pending_queue(
                    time.time(),
                    last_queue_flush,
                    last_xuni_flush,
                    xuni_flush_interval_s,
                )

                if now - last_ui >= self.settings.stats_interval_s:
                    if not self.use_dashboard:
                        self._log("info", self.metrics.summary_line(snap))
                    else:
                        self._ui_refresh(snap)
                    last_ui = now

        except KeyboardInterrupt:
            self._graceful_shutdown("Interrupted by user (Ctrl+C)")
        finally:
            if not self._shutting_down:
                self._graceful_shutdown("Shutting down")
            self._finalize_session()
