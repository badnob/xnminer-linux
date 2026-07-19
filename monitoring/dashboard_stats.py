from __future__ import annotations

from datetime import date

from core.models import MiningStats
from monitoring.rewards import blocks_to_tokens, reward_per_block

_BLOCK_KINDS = ("XUNI", "XNM", "XBLK")
_EMPTY = {k: 0 for k in _BLOCK_KINDS}


def _kind_count(mapping: dict[str, int], kind: str) -> int:
    return int(mapping.get(kind, 0))


def found_by_kind(stats: MiningStats) -> dict[str, int]:
    """Session finds — raw block counts (not yet rewarded until accepted)."""
    return {
        "XUNI": stats.found_xuni,
        "XNM": stats.found_xnm,
        "XBLK": stats.found_xblk,
    }


def accepted_blocks_by_kind(stats: MiningStats) -> dict[str, int]:
    return {
        "XUNI": stats.accepted_live_xuni + stats.accepted_flush_xuni,
        "XNM": stats.accepted_live_xnm + stats.accepted_flush_xnm,
        "XBLK": stats.accepted_live_xblk + stats.accepted_flush_xblk,
    }


def accepted_by_kind(stats: MiningStats) -> dict[str, int]:
    """Back-compat alias: accepted **block** counts."""
    return accepted_blocks_by_kind(stats)


def accepted_tokens_by_kind(
    stats: MiningStats,
    *,
    on: date | None = None,
) -> dict[str, float]:
    """Accepted amounts in tokens using the current (or given) reward era."""
    blocks = accepted_blocks_by_kind(stats)
    return {
        kind: blocks_to_tokens(kind, blocks[kind], on) for kind in _BLOCK_KINDS
    }


def rejected_by_kind(stats: MiningStats) -> dict[str, int]:
    return {
        "XUNI": stats.rejected_live_xuni + stats.rejected_flush_xuni,
        "XNM": stats.rejected_live_xnm + stats.rejected_flush_xnm,
        "XBLK": stats.rejected_live_xblk + stats.rejected_flush_xblk,
    }


def queued_by_kind(
    pending_by_type: dict[str, int] | None,
    *,
    resubmission: bool,
) -> dict[str, int]:
    pending = pending_by_type or _EMPTY
    return {kind: _kind_count(pending, kind) for kind in _BLOCK_KINDS}


def row_total(values: dict[str, int] | dict[str, float]) -> float:
    return float(sum(values[kind] for kind in _BLOCK_KINDS))


def dashboard_rows(
    stats: MiningStats,
    pending_by_type: dict[str, int] | None,
    resubmission_by_type: dict[str, int] | None,
    *,
    on: date | None = None,
) -> list[tuple[str, dict[str, float], str]]:
    """Return dashboard table rows: (label, values, unit) in display order.

    unit is 'blocks' or 'tokens'. Accepted is shown as tokens (halving-aware).
    """
    return [
        ("Found (session)", {k: float(v) for k, v in found_by_kind(stats).items()}, "blocks"),
        ("Accepted (today)", accepted_tokens_by_kind(stats, on=on), "tokens"),
        ("Rejected (pool)", {k: float(v) for k, v in rejected_by_kind(stats).items()}, "blocks"),
        (
            "Queued",
            {k: float(v) for k, v in queued_by_kind(pending_by_type, resubmission=False).items()},
            "blocks",
        ),
        (
            "Resubmit",
            {
                k: float(v)
                for k, v in queued_by_kind(resubmission_by_type, resubmission=True).items()
            },
            "blocks",
        ),
    ]


def format_reward_hint(on: date | None = None) -> str:
    return (
        f"rewards: XNM {reward_per_block('XNM', on):g}/blk · "
        f"XUNI {reward_per_block('XUNI', on):g}/blk · "
        f"XBLK {reward_per_block('XBLK', on):g}/blk"
    )
