"""VRAM ↔ CUDA batch sizing — mirrors native HashApiTuning.cpp."""

from __future__ import annotations

from dataclasses import dataclass

# xen_cuda / HashApiTuning.cpp subtracts this from free memory before sizing.
CUDA_ENGINE_RESERVE_BYTES = 100 * 1024 * 1024
BYTES_PER_ATTEMPT_FACTOR = 1.001
# Driver + context memory not included in batch×difficulty estimate (see session logs).
DEFAULT_CUDA_RUNTIME_OVERHEAD_MIB = 2048


@dataclass(frozen=True)
class CudaVramPlan:
    """Batch choice tied to configured VRAM limits."""

    batch_size: int
    lanes: int
    batch_per_lane: int
    lane_reserve: int
    budget_bytes: int
    budget_mib: int
    batch_vram_bytes: int
    batch_vram_mib: int
    used_before_mib: int
    projected_used_mib: int
    projected_headroom_mib: int
    runtime_overhead_mib: int
    target_mib: int
    effective_target_mib: int
    vram_scale: float
    desktop_headroom_mib: int
    difficulty: int

    def summary(self) -> str:
        lane_part = (
            f"lanes={self.lanes}×{self.batch_per_lane:,}"
            if self.lanes > 1
            else f"batch={self.batch_size:,}"
        )
        reserve = (
            f" reserve={self.lane_reserve}"
            if self.lane_reserve > 0 and self.lanes >= self.lane_reserve
            else ""
        )
        return (
            f"{lane_part}{reserve} "
            f"budget={self.budget_mib:,}MiB "
            f"batch_vram≈{self.batch_vram_mib:,}MiB "
            f"cuda_overhead={self.runtime_overhead_mib:,}MiB "
            f"projected_used={self.projected_used_mib:,}MiB "
            f"projected_free={self.projected_headroom_mib:,}MiB "
            f"(target≤{self.target_mib:,}MiB desktop≥{self.desktop_headroom_mib:,}MiB)"
        )

    def within_limits(self) -> bool:
        return (
            self.projected_used_mib <= self.target_mib
            and self.projected_headroom_mib >= self.desktop_headroom_mib
        )

    def fills_budget(self, *, tolerance_mib: int = 2) -> bool:
        """True when batch VRAM uses the full configured cap (harvest push)."""
        return abs(self.batch_vram_mib - self.budget_mib) <= tolerance_mib


def bytes_per_attempt(difficulty: int) -> float:
    return float(difficulty) * 1024.0 * BYTES_PER_ATTEMPT_FACTOR


def vram_budget_bytes(
    total_bytes: int,
    free_bytes: int,
    *,
    target_mib: int,
    desktop_headroom_mib: int,
) -> int:
    """
    Bytes the next CUDA batch may consume while respecting:
    - total GPU usage stays at or below target_mib
    - at least desktop_headroom_mib remains free for the OS/console
    """
    used = max(0, total_bytes - free_bytes)
    target_b = target_mib * 1024 * 1024
    headroom_b = desktop_headroom_mib * 1024 * 1024
    under_target = max(0, target_b - used)
    under_headroom = max(0, free_bytes - headroom_b)
    return min(under_target, under_headroom)


def cuda_lane_count(
    difficulty: int,
    *,
    reference_difficulty: int,
    max_lanes: int,
) -> int:
    """
    Spin up more lanes when difficulty drops below the reference.

    At reference_difficulty (e.g. 1100) one lane fills the VRAM cap. Lower
    difficulty uses lighter per-attempt memory, so we split the same cap across
    more lanes (distinct key prefixes) instead of leaving VRAM idle.
    """
    if max_lanes <= 1 or reference_difficulty <= 0 or difficulty <= 0:
        return 1
    if difficulty >= reference_difficulty:
        return 1
    boost = reference_difficulty // difficulty
    return max(1, min(max_lanes, boost))


def vram_cap_batch_budget_bytes(
    total_bytes: int,
    *,
    target_mib: int,
    desktop_headroom_mib: int,
    runtime_overhead_mib: int,
) -> tuple[int, int]:
    """
    Full batch-buffer budget from miner.ini caps.

    Live NVML free memory is intentionally ignored so difficulty changes and
    mid-session replans use the same steady-state formula.
    """
    total_mib = total_bytes // (1024 * 1024)
    effective_target_mib = target_mib
    cap_batch_mib = max(0, effective_target_mib - runtime_overhead_mib)
    headroom_limited_mib = max(
        0, total_mib - desktop_headroom_mib - runtime_overhead_mib
    )
    allowed_mib = min(cap_batch_mib, headroom_limited_mib)
    return allowed_mib * 1024 * 1024, effective_target_mib


def memory_limited_batch_size(
    free_vram_bytes: int,
    difficulty: int,
    *,
    reserve_bytes: int = CUDA_ENGINE_RESERVE_BYTES,
) -> int:
    """Same formula as hashapi::estimateCudaMemoryBatchLimit."""
    if difficulty <= 0 or free_vram_bytes <= reserve_bytes:
        return 0
    available = float(free_vram_bytes - reserve_bytes)
    per_attempt = bytes_per_attempt(difficulty)
    if per_attempt <= 0:
        return 0
    return int(available / per_attempt)


def recommended_batch_size(difficulty: int) -> int:
    """Same tiers as hashapi::recommendedCudaBatchSize."""
    if difficulty <= 1:
        return 2048
    if difficulty <= 8:
        return 4096
    if difficulty <= 64:
        return 3072
    return 0


def select_batch_size(
    budget_bytes: int,
    difficulty: int,
    *,
    explicit_max_batch: int = 0,
    fill_vram_cap: bool = True,
) -> int:
    """
    Pick batch size from a VRAM budget byte allowance.

    Pass budget_bytes + CUDA_ENGINE_RESERVE_BYTES to the native DLL if calling
    xen_cuda_select_batch_size directly — the DLL subtracts the reserve itself.

    When fill_vram_cap is True (default), use the full difficulty-scaled budget.
    """
    # Native code treats the argument as free_vram_bytes.
    dll_free_arg = budget_bytes + CUDA_ENGINE_RESERVE_BYTES
    memory_limit = memory_limited_batch_size(dll_free_arg, difficulty)
    if memory_limit <= 0:
        return 0

    if explicit_max_batch > 0:
        return min(memory_limit, explicit_max_batch)

    if not fill_vram_cap:
        tuned = recommended_batch_size(difficulty)
        if tuned > 0:
            return min(memory_limit, tuned)
    return memory_limit


def estimate_batch_vram_bytes(batch_size: int, difficulty: int) -> int:
    """Approximate batch footprint (matches MineUnit.cpp usedMemory estimate)."""
    if batch_size <= 0 or difficulty <= 0:
        return 0
    return int(batch_size * bytes_per_attempt(difficulty))


def _plan_projection(
    *,
    total_mib: int,
    lanes: int,
    batch_per_lane: int,
    difficulty: int,
    runtime_overhead_mib: int,
) -> tuple[int, int, int, int]:
    batch_vram_bytes = estimate_batch_vram_bytes(batch_per_lane, difficulty) * lanes
    batch_vram_mib = batch_vram_bytes // (1024 * 1024)
    projected_used_mib = batch_vram_mib + runtime_overhead_mib
    projected_headroom_mib = max(0, total_mib - projected_used_mib)
    return batch_vram_bytes, batch_vram_mib, projected_used_mib, projected_headroom_mib


def clamp_plan_to_caps(plan: CudaVramPlan) -> CudaVramPlan:
    """
    Shrink batch/lanes until projected VRAM fits miner.ini caps.

    Prefers keeping lane count during harvest (lower difficulty) so parallel
    key-prefix search stays wide; trims per-lane batch first, then lanes.
    """
    if plan.within_limits():
        return plan

    total_mib = plan.projected_used_mib + plan.projected_headroom_mib
    lanes = plan.lanes
    batch_per_lane = plan.batch_per_lane

    for _ in range(10_000):
        batch_vram_bytes, batch_vram_mib, projected_used_mib, projected_headroom_mib = (
            _plan_projection(
                total_mib=total_mib,
                lanes=lanes,
                batch_per_lane=batch_per_lane,
                difficulty=plan.difficulty,
                runtime_overhead_mib=plan.runtime_overhead_mib,
            )
        )
        if (
            projected_used_mib <= plan.target_mib
            and projected_headroom_mib >= plan.desktop_headroom_mib
        ):
            return CudaVramPlan(
                batch_size=batch_per_lane,
                lanes=lanes,
                batch_per_lane=batch_per_lane,
                lane_reserve=plan.lane_reserve,
                budget_bytes=plan.budget_bytes,
                budget_mib=plan.budget_mib,
                batch_vram_bytes=batch_vram_bytes,
                batch_vram_mib=batch_vram_mib,
                used_before_mib=plan.used_before_mib,
                projected_used_mib=projected_used_mib,
                projected_headroom_mib=projected_headroom_mib,
                runtime_overhead_mib=plan.runtime_overhead_mib,
                target_mib=plan.target_mib,
                effective_target_mib=plan.effective_target_mib,
                vram_scale=plan.vram_scale,
                desktop_headroom_mib=plan.desktop_headroom_mib,
                difficulty=plan.difficulty,
            )

        if batch_per_lane > 1:
            batch_per_lane = max(1, int(batch_per_lane * 0.98))
            continue

        if lanes > 1:
            lanes -= 1
            per_lane_budget = max(1, plan.budget_bytes // lanes)
            batch_per_lane = select_batch_size(
                per_lane_budget,
                plan.difficulty,
                explicit_max_batch=0,
                fill_vram_cap=True,
            )
            continue

        break

    return plan


def plan_cuda_batch(
    total_bytes: int,
    free_bytes: int,
    *,
    target_mib: int,
    desktop_headroom_mib: int,
    difficulty: int,
    reference_difficulty: int,
    max_lanes: int = 4,
    lane_reserve: int = 1,
    explicit_batch: int = 0,
    explicit_max_batch: int = 0,
    runtime_overhead_mib: int = DEFAULT_CUDA_RUNTIME_OVERHEAD_MIB,
) -> CudaVramPlan:
    """Size CUDA batch/lanes from miner.ini VRAM caps (not live free memory)."""
    total_mib = total_bytes // (1024 * 1024)
    used_before_mib = max(0, (total_bytes - free_bytes) // (1024 * 1024))
    budget_bytes, effective_target_mib = vram_cap_batch_budget_bytes(
        total_bytes,
        target_mib=target_mib,
        desktop_headroom_mib=desktop_headroom_mib,
        runtime_overhead_mib=runtime_overhead_mib,
    )
    budget_mib = budget_bytes // (1024 * 1024)
    lanes = cuda_lane_count(
        difficulty,
        reference_difficulty=reference_difficulty,
        max_lanes=max(1, max_lanes),
    )
    reserve = max(0, lane_reserve)
    per_lane_budget = max(1, budget_bytes // max(1, lanes))

    max_batch_per_lane = select_batch_size(
        per_lane_budget,
        difficulty,
        explicit_max_batch=explicit_max_batch,
        fill_vram_cap=True,
    )
    if max_batch_per_lane <= 0:
        batch_per_lane = 0
    elif explicit_batch > 0:
        batch_per_lane = min(explicit_batch, max_batch_per_lane)
    else:
        batch_per_lane = max_batch_per_lane

    batch_size = batch_per_lane
    batch_vram_bytes = estimate_batch_vram_bytes(batch_per_lane, difficulty) * lanes
    batch_vram_mib = batch_vram_bytes // (1024 * 1024)
    projected_used_mib = batch_vram_mib + runtime_overhead_mib
    projected_headroom_mib = max(0, total_mib - projected_used_mib)

    plan = CudaVramPlan(
        batch_size=batch_size,
        lanes=lanes,
        batch_per_lane=batch_per_lane,
        lane_reserve=reserve,
        budget_bytes=budget_bytes,
        budget_mib=budget_mib,
        batch_vram_bytes=batch_vram_bytes,
        batch_vram_mib=batch_vram_mib,
        used_before_mib=used_before_mib,
        projected_used_mib=projected_used_mib,
        projected_headroom_mib=projected_headroom_mib,
        runtime_overhead_mib=runtime_overhead_mib,
        target_mib=target_mib,
        effective_target_mib=effective_target_mib,
        vram_scale=1.0,
        desktop_headroom_mib=desktop_headroom_mib,
        difficulty=difficulty,
    )
    return clamp_plan_to_caps(plan)