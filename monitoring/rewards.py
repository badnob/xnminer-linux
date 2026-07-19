"""XenBlocks token rewards and yearly XNM halving schedule.

Official schedule (docs.xenblocks.io/mining/xnm):
  Year 1: 10 XNM / block, Year 2: 5, Year 3: 2.5, … (halve each year).

Mining started September 2023. The second yearly halving (5 → 2.5) landed
around mid-September 2025, so genesis is anchored to 2023-09-13.

XUNI and XBLK are not subject to the XNM halving; each accepted block pays 1 token.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# First day of year-1 rewards (10 XNM). Adjust only if the network publishes a
# more precise genesis; day length for "year" is 365 days from this date.
XNM_GENESIS_DATE = date(2023, 9, 13)
XNM_INITIAL_REWARD = 10.0
_YEAR_DAYS = 365

# Fixed per-block rewards for non-halving tokens.
XUNI_REWARD = 1.0
XBLK_REWARD = 1.0


def xnm_year_index(on: date | None = None) -> int:
    """0-based year index since genesis (0 = first year @ 10 XNM)."""
    on = on or date.today()
    if on < XNM_GENESIS_DATE:
        return 0
    return (on - XNM_GENESIS_DATE).days // _YEAR_DAYS


def xnm_reward_per_block(on: date | None = None) -> float:
    """XNM paid for one accepted XNM block on the given date."""
    idx = xnm_year_index(on)
    return XNM_INITIAL_REWARD / (2**idx)


def reward_per_block(kind: str, on: date | None = None) -> float:
    """Token amount paid for one accepted block of the given kind."""
    upper = (kind or "").upper()
    if upper == "XNM":
        return xnm_reward_per_block(on)
    if upper == "XUNI":
        return XUNI_REWARD
    if upper == "XBLK":
        return XBLK_REWARD
    return 1.0


def blocks_to_tokens(kind: str, blocks: int | float, on: date | None = None) -> float:
    return float(blocks) * reward_per_block(kind, on)


def current_reward_summary(on: date | None = None) -> str:
    """Short label for UI, e.g. 'XNM 2.5 · XUNI 1 · XBLK 1'."""
    on = on or date.today()
    return (
        f"XNM {xnm_reward_per_block(on):g} · "
        f"XUNI {XUNI_REWARD:g} · "
        f"XBLK {XBLK_REWARD:g}"
    )


def next_halving_date(on: date | None = None) -> date:
    """First day of the next XNM reward year."""
    on = on or date.today()
    idx = xnm_year_index(on)
    return XNM_GENESIS_DATE + timedelta(days=(idx + 1) * _YEAR_DAYS)


def reward_era_label(on: date | None = None) -> str:
    on = on or date.today()
    year = xnm_year_index(on) + 1
    reward = xnm_reward_per_block(on)
    return f"year {year} · {reward:g} XNM/block"
