"""Native CUDA GPU backend — libxen_cuda.so / xen_cuda.dll, no legacy binary."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from config.settings import Settings
from core.models import BlockHit
from mining.base import MineBatchResult, MinerBackend
from mining.block_types import classify_block
from mining.cuda_engine import CudaBatchResult, CudaEngine
from efficiency.cuda_lane_policy import (
    CudaLanePolicyState,
    load_lane_policy,
    restore_lane_cap_if_cool,
    save_lane_policy,
)
from efficiency.vram_policy import VramCaps, resolve_vram_caps
from mining.vram_batch import (
    CUDA_ENGINE_RESERVE_BYTES,
    CudaVramPlan,
    memory_limited_batch_size,
    plan_cuda_batch,
)


from block_queue.policy import in_xuni_window


def _lane_prefix(lane: int) -> str:
    return f"{lane:04x}"


class CudaNativeBackend(MinerBackend):
    def __init__(self, settings: Settings, strategy_name: str) -> None:
        self.settings = settings
        self.strategy_name = strategy_name
        self._engine = CudaEngine(settings.cuda_dll_path)
        self._lanes = 1
        self._batch_per_lane = settings.cuda_batch_size
        self._batch_size = settings.cuda_batch_size
        self._difficulty = settings.memory_cost
        self._started = False
        self._last_hs = 0.0
        self._vram_plan: CudaVramPlan | None = None
        self._abort_check: Callable[[], bool] | None = None
        data_dir = settings.log_path.parent
        self._lane_policy_path = data_dir / "gpu_lane_cap.json"
        self._temp_lane_log_path = data_dir / "gpu_temp_lane.log"
        self._lane_policy = load_lane_policy(
            self._lane_policy_path,
            config_max_lanes=settings.cuda_max_lanes,
        )
        self._max_lanes_cap = self._lane_policy.effective_max_lanes
        self._lane_workers_dir = data_dir / "cuda_lane_workers"
        self._lane_engines: dict[int, CudaEngine] = {}
        self._parallel_mode = "sequential"
        self._vram_caps: VramCaps | None = None

    def set_vram_caps(self, caps: VramCaps | None) -> None:
        self._vram_caps = caps

    def _caps_for_total(self, total_mib: int) -> VramCaps:
        if self._vram_caps is not None and self._vram_caps.total_mib == total_mib:
            return self._vram_caps
        s = self.settings
        caps = resolve_vram_caps(
            total_mib,
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
        self._vram_caps = caps
        return caps

    def _plan_from_device(self, info, *, difficulty: int | None = None) -> CudaVramPlan:
        diff = self._difficulty if difficulty is None else difficulty
        explicit = self.settings.cuda_batch_size if self.settings.cuda_batch_size > 0 else 0
        total_mib = max(1, int(info.total_vram_bytes) // (1024 * 1024))
        caps = self._caps_for_total(total_mib)
        plan = plan_cuda_batch(
            int(info.total_vram_bytes),
            int(info.free_vram_bytes),
            target_mib=caps.target_mib,
            desktop_headroom_mib=caps.headroom_mib,
            difficulty=diff,
            reference_difficulty=self.settings.vram_reference_difficulty,
            max_lanes=self._max_lanes_cap,
            lane_reserve=self.settings.cuda_lane_reserve,
            explicit_batch=explicit,
            explicit_max_batch=self.settings.cuda_max_batch_size,
            runtime_overhead_mib=caps.runtime_overhead_mib,
        )
        if plan.batch_per_lane <= 0:
            raise RuntimeError(
                f"Could not size CUDA batch for {diff} difficulty: "
                f"budget={plan.budget_mib}MiB "
                f"target={caps.target_mib}MiB "
                f"headroom={caps.headroom_mib}MiB "
                f"gpu_total={caps.total_mib}MiB"
            )
        if not plan.within_limits():
            raise RuntimeError(
                f"CUDA VRAM plan violates limits: {plan.summary()}"
            )
        if (
            diff < self.settings.vram_reference_difficulty
            and not plan.fills_budget()
        ):
            raise RuntimeError(
                f"CUDA harvest plan under-filled VRAM cap: {plan.summary()}"
            )
        return plan

    def _apply_plan(self, plan: CudaVramPlan) -> None:
        self._vram_plan = plan
        self._lanes = plan.lanes
        self._batch_per_lane = plan.batch_per_lane
        self._batch_size = plan.batch_per_lane

    def _lane_lib_path(self, lane: int) -> Path:
        # Separate file paths so the OS loads each as its own module/image
        # (needed for multi-lane when native parallel lanes are unavailable).
        suffix = Path(self.settings.cuda_dll_path).suffix or ".so"
        return self._lane_workers_dir / f"lane{lane}{suffix}"

    def _ensure_lane_dll(self, lane: int) -> Path:
        src = Path(self.settings.cuda_dll_path)
        dst = self._lane_lib_path(lane)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)
        return dst

    def _worker_reserve_bytes(self) -> int:
        caps = getattr(self, "_vram_caps", None)
        if caps is not None:
            return caps.runtime_overhead_mib * 1024 * 1024
        if self.settings.cuda_runtime_overhead_mib > 0:
            return self.settings.cuda_runtime_overhead_mib * 1024 * 1024
        # Fallback before first plan: absolute floor from settings.
        return max(256, int(self.settings.runtime_overhead_floor_mib)) * 1024 * 1024

    def _teardown_lane_copies(self) -> None:
        for engine in self._lane_engines.values():
            engine.shutdown()
        self._lane_engines.clear()

    def _sync_lane_copy_workers(self) -> None:
        needed = set(range(1, self._lanes))
        for lane in list(self._lane_engines):
            if lane not in needed:
                self._lane_engines[lane].shutdown()
                del self._lane_engines[lane]
        reserve = self._worker_reserve_bytes()
        for lane in sorted(needed):
            if lane in self._lane_engines:
                continue
            dll = self._ensure_lane_dll(lane)
            engine = CudaEngine(dll)
            engine.init(device_id=0, reserve_bytes=reserve)
            self._lane_engines[lane] = engine
        self._parallel_mode = "dll-copies"

    def _sync_lane_engines(self) -> None:
        if not self._started:
            return
        if self._engine.parallel_lanes_supported:
            self._teardown_lane_copies()
            self._parallel_mode = "native" if self._lanes > 1 else "sequential"
            self._engine.set_lane_count(self._lanes)
            return
        if self._lanes > 1:
            self._sync_lane_copy_workers()
            return
        self._teardown_lane_copies()
        self._parallel_mode = "sequential"

    def _engine_for_lane(self, lane: int) -> CudaEngine:
        if lane == 0 or self._engine.parallel_lanes_supported:
            return self._engine
        return self._lane_engines[lane]

    def _verify_batch_fits_budget(self, plan: CudaVramPlan, difficulty: int) -> None:
        per_lane_budget = max(1, plan.budget_bytes // max(1, plan.lanes))
        dll_free_arg = per_lane_budget + CUDA_ENGINE_RESERVE_BYTES
        memory_limit = memory_limited_batch_size(dll_free_arg, difficulty)
        if self.settings.cuda_max_batch_size > 0:
            memory_limit = min(memory_limit, self.settings.cuda_max_batch_size)
        if plan.batch_per_lane > memory_limit:
            raise RuntimeError(
                f"CUDA batch exceeds VRAM cap: batch={plan.batch_per_lane} "
                f"limit={memory_limit} lane_budget={per_lane_budget // (1024 * 1024)}MiB"
            )

    def start(self) -> None:
        if self._started:
            return
        reserve = self.settings.headroom_mib * 1024 * 1024
        self._engine.init(device_id=0, reserve_bytes=reserve)
        info = self._engine.device_info(0)
        plan = self._plan_from_device(info)
        self._verify_batch_fits_budget(plan, self._difficulty)
        self._apply_plan(plan)
        self._started = True
        self._sync_lane_engines()

    def stop(self) -> None:
        if self._started:
            self._teardown_lane_copies()
            self._engine.shutdown()
            self._started = False
            self._vram_plan = None
            self._parallel_mode = "sequential"

    def set_lanes(self, lanes: int) -> None:
        # Lane count is owned by the VRAM plan (difficulty-driven), not manual tuning.
        return

    def set_abort_check(self, check: Callable[[], bool] | None) -> None:
        self._abort_check = check

    @property
    def max_lanes_cap(self) -> int:
        return self._max_lanes_cap

    @property
    def lane_policy(self) -> CudaLanePolicyState:
        return self._lane_policy

    @property
    def lane_policy_path(self):
        return self._lane_policy_path

    def reduce_lane_cap(self) -> int:
        """Lower the lane ceiling by one after a temp cooldown (minimum 1)."""
        new_cap = max(1, self._max_lanes_cap - 1)
        if new_cap == self._max_lanes_cap:
            return new_cap
        self._max_lanes_cap = new_cap
        self._lane_policy.effective_max_lanes = new_cap
        save_lane_policy(self._lane_policy_path, self._lane_policy)
        return new_cap

    def restore_lane_cap_if_cool(
        self,
        temperature_c: int,
        *,
        difficulty: int | None = None,
    ) -> bool:
        """Restore full lane cap when reference difficulty and temps are safe."""
        diff = self._difficulty if difficulty is None else difficulty
        policy, restored = restore_lane_cap_if_cool(
            self._lane_policy_path,
            self._temp_lane_log_path,
            self._lane_policy,
            temperature_c=temperature_c,
            warn_temp_c=self.settings.warn_gpu_temp_c,
            difficulty=diff,
            reference_difficulty=self.settings.vram_reference_difficulty,
        )
        if not restored:
            return False
        self._lane_policy = policy
        self._max_lanes_cap = policy.effective_max_lanes
        return True

    def set_difficulty(self, difficulty: int) -> None:
        if difficulty == self._difficulty:
            return
        self._difficulty = difficulty
        info = self._engine.device_info(0)
        plan = self._plan_from_device(info, difficulty=difficulty)
        self._verify_batch_fits_budget(plan, difficulty)
        self._apply_plan(plan)
        self._sync_lane_engines()

    def _run_lane(self, lane: int) -> CudaBatchResult:
        engine = self._engine_for_lane(lane)
        kwargs = {
            "salt_hex": self.settings.salt_hex,
            "difficulty": self._difficulty,
            "batch_size": self._batch_per_lane,
            "key_prefix": _lane_prefix(lane),
            "allow_xuni": in_xuni_window(),
        }
        if engine is self._engine and self._engine.parallel_lanes_supported:
            result = engine.run_lane_batch(lane, **kwargs)
        else:
            result = engine.run_batch(**kwargs)
        if not result.ok:
            raise RuntimeError(result.error or f"CUDA lane {lane} batch failed")
        return result

    def _hit_from_result(self, result: CudaBatchResult) -> BlockHit | None:
        if not result.matches:
            return None
        m = result.matches[0]
        kind = classify_block(m.hash_str, m.pattern)
        return BlockHit(
            key=m.key,
            hash_str=m.hash_str,
            block_type=kind,
            attempts=int(result.attempts),
            strategy=self.strategy_name,
            hps=result.hashrate,
            memory_cost=self._difficulty,
        )

    def mine_batch(self, batch_size: int) -> MineBatchResult:
        if not self._started:
            return MineBatchResult(hashes_done=0, hit=None)

        if self._abort_check and self._abort_check():
            reason = (
                f"GPU temp limit ({self.settings.max_gpu_temp_c}C) — "
                f"aborted before batch"
            )
            return MineBatchResult(hashes_done=0, hit=None, aborted=True, abort_reason=reason)

        total_hashes = 0
        total_hs = 0.0
        hit = None
        lanes_done = 0
        abort_reason = ""

        parallel = self._lanes > 1 and self._parallel_mode in ("native", "dll-copies")
        if not parallel:
            total_hashes = 0
            total_hs = 0.0
            hit = None
            lanes_done = 0
            for lane in range(self._lanes):
                if self._abort_check and self._abort_check():
                    abort_reason = (
                        f"GPU temp limit ({self.settings.max_gpu_temp_c}C) — "
                        f"stopped after lane {lanes_done}/{self._lanes}"
                    )
                    break
                result = self._run_lane(lane)
                lanes_done += 1
                total_hashes += int(result.attempts)
                total_hs += result.hashrate
                if hit is None:
                    hit = self._hit_from_result(result)
            self._last_hs = total_hs
            return MineBatchResult(
                hashes_done=total_hashes,
                hit=hit,
                aborted=bool(abort_reason),
                abort_reason=abort_reason,
            )

        with ThreadPoolExecutor(max_workers=self._lanes) as pool:
            futures = {
                pool.submit(self._run_lane, lane): lane
                for lane in range(self._lanes)
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception:
                    for pending in futures:
                        pending.cancel()
                    raise
                lanes_done += 1
                total_hashes += int(result.attempts)
                total_hs += result.hashrate
                if hit is None:
                    hit = self._hit_from_result(result)

        if self._abort_check and self._abort_check():
            abort_reason = (
                f"GPU temp limit ({self.settings.max_gpu_temp_c}C) — "
                f"stopped after parallel lanes ({lanes_done}/{self._lanes})"
            )

        self._last_hs = total_hs
        return MineBatchResult(
            hashes_done=total_hashes,
            hit=hit,
            aborted=bool(abort_reason),
            abort_reason=abort_reason,
        )

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def active_lanes(self) -> int:
        return self._lanes

    @property
    def batch_size(self) -> int:
        return self._batch_per_lane

    @property
    def batch_per_lane(self) -> int:
        return self._batch_per_lane

    @property
    def difficulty(self) -> int:
        return self._difficulty

    @property
    def hashrate(self) -> float:
        return self._last_hs

    @property
    def vram_plan(self) -> CudaVramPlan | None:
        return self._vram_plan

    @property
    def parallel_mode(self) -> str:
        return self._parallel_mode