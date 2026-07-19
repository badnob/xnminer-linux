"""VRAM safety caps as percentages of each GPU's total memory.

Your working 5090 profile (≈32 627 MiB total) used fixed MiB values that
map to roughly:

  target used     22 528 MiB  →  ~69% of total
  desktop free     8 192 MiB  →  ~25% of total
  emergency used  30 252 MiB  →  ~93% of total
  min free         1 200 MiB  →  ~4% of total
  CUDA overhead    2 048 MiB  →  ~6% of total

Those ratios are applied to every card so low-end GPUs keep proportional
headroom instead of a fixed 8 GB free (which starves 8–12 GB cards).
"""

from __future__ import annotations

from dataclasses import dataclass

# Reference total used when reverse-engineering the original fixed caps.
REFERENCE_TOTAL_MIB = 32607
REFERENCE_TARGET_MIB = 22528
REFERENCE_HEADROOM_MIB = 8192
REFERENCE_EMERGENCY_MIB = 30252
REFERENCE_MIN_HEADROOM_MIB = 1200
REFERENCE_OVERHEAD_MIB = 2048

# Defaults = that profile as percentages (rounded).
DEFAULT_TARGET_VRAM_PCT = 100.0 * REFERENCE_TARGET_MIB / REFERENCE_TOTAL_MIB  # ~69.09
DEFAULT_DESKTOP_HEADROOM_PCT = 100.0 * REFERENCE_HEADROOM_MIB / REFERENCE_TOTAL_MIB  # ~25.12
DEFAULT_EMERGENCY_VRAM_PCT = 100.0 * REFERENCE_EMERGENCY_MIB / REFERENCE_TOTAL_MIB  # ~92.78
DEFAULT_MIN_HEADROOM_PCT = 100.0 * REFERENCE_MIN_HEADROOM_MIB / REFERENCE_TOTAL_MIB  # ~3.68
DEFAULT_RUNTIME_OVERHEAD_PCT = 100.0 * REFERENCE_OVERHEAD_MIB / REFERENCE_TOTAL_MIB  # ~6.28

# Absolute floors so tiny cards never leave zero free / zero overhead.
DEFAULT_MIN_HEADROOM_FLOOR_MIB = 512
DEFAULT_OVERHEAD_FLOOR_MIB = 256
DEFAULT_EMERGENCY_FREE_FLOOR_MIB = 256


@dataclass(frozen=True)
class VramCaps:
    """Resolved absolute MiB caps for one GPU."""

    total_mib: int
    target_mib: int
    headroom_mib: int
    emergency_mib: int
    min_headroom_mib: int
    runtime_overhead_mib: int
    target_pct: float
    headroom_pct: float
    emergency_pct: float
    min_headroom_pct: float
    overhead_pct: float

    def summary(self) -> str:
        return (
            f"VRAM caps on {self.total_mib:,}MiB GPU: "
            f"target≤{self.target_mib:,}MiB ({self.target_pct:.0f}%) "
            f"desktop≥{self.headroom_mib:,}MiB free ({self.headroom_pct:.0f}%) "
            f"emergency≥{self.emergency_mib:,}MiB used ({self.emergency_pct:.0f}%) "
            f"min_free={self.min_headroom_mib:,}MiB "
            f"cuda_overhead={self.runtime_overhead_mib:,}MiB"
        )


def _clamp_pct(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def resolve_vram_caps(
    total_mib: int,
    *,
    target_pct: float = DEFAULT_TARGET_VRAM_PCT,
    desktop_headroom_pct: float = DEFAULT_DESKTOP_HEADROOM_PCT,
    emergency_vram_pct: float = DEFAULT_EMERGENCY_VRAM_PCT,
    min_headroom_pct: float = DEFAULT_MIN_HEADROOM_PCT,
    runtime_overhead_pct: float = DEFAULT_RUNTIME_OVERHEAD_PCT,
    min_headroom_floor_mib: int = DEFAULT_MIN_HEADROOM_FLOOR_MIB,
    overhead_floor_mib: int = DEFAULT_OVERHEAD_FLOOR_MIB,
    emergency_free_floor_mib: int = DEFAULT_EMERGENCY_FREE_FLOOR_MIB,
    # Optional absolute overrides (0 = unused).
    target_mib_override: int = 0,
    headroom_mib_override: int = 0,
    emergency_mib_override: int = 0,
    min_headroom_mib_override: int = 0,
    runtime_overhead_mib_override: int = 0,
) -> VramCaps:
    """Turn percentage policy into absolute MiB for this GPU."""
    total = max(1, int(total_mib))

    t_pct = _clamp_pct(target_pct)
    h_pct = _clamp_pct(desktop_headroom_pct)
    e_pct = _clamp_pct(emergency_vram_pct)
    m_pct = _clamp_pct(min_headroom_pct)
    o_pct = _clamp_pct(runtime_overhead_pct)

    if target_mib_override > 0:
        target = int(target_mib_override)
    else:
        target = int(total * t_pct / 100.0)

    if headroom_mib_override > 0:
        headroom = int(headroom_mib_override)
    else:
        headroom = int(total * h_pct / 100.0)

    if emergency_mib_override > 0:
        emergency = int(emergency_mib_override)
    else:
        emergency = int(total * e_pct / 100.0)

    if min_headroom_mib_override > 0:
        min_head = int(min_headroom_mib_override)
    else:
        min_head = int(total * m_pct / 100.0)

    if runtime_overhead_mib_override > 0:
        overhead = int(runtime_overhead_mib_override)
    else:
        overhead = int(total * o_pct / 100.0)

    # Floors / clamps so the policy stays feasible on every card size.
    headroom = max(int(min_headroom_floor_mib), min(headroom, max(0, total - 1)))
    min_head = max(int(min_headroom_floor_mib), min(min_head, headroom))
    overhead = max(int(overhead_floor_mib), min(overhead, max(0, total // 2)))
    # Always leave some free at emergency.
    emergency = min(emergency, max(0, total - int(emergency_free_floor_mib)))
    emergency = max(emergency, min_head)  # emergency used limit above min free zone
    # Target must leave desktop headroom possible.
    target = min(target, max(0, total - headroom))
    target = max(0, target)
    # Overhead cannot eat the entire target budget.
    if target > 0:
        overhead = min(overhead, max(overhead_floor_mib, target // 2))

    return VramCaps(
        total_mib=total,
        target_mib=target,
        headroom_mib=headroom,
        emergency_mib=emergency,
        min_headroom_mib=min_head,
        runtime_overhead_mib=overhead,
        target_pct=t_pct,
        headroom_pct=h_pct,
        emergency_pct=e_pct,
        min_headroom_pct=m_pct,
        overhead_pct=o_pct,
    )
