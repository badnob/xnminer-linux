from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from monitoring.periods import (
    format_day_label,
    format_week_label,
    mining_day,
    mining_week_days,
    previous_mining_day,
    previous_mining_week_days,
    previous_mining_week_start,
)
from monitoring.rewards import blocks_to_tokens, current_reward_summary, reward_era_label

_BLOCK_KINDS = ("XUNI", "XNM", "XBLK")
_EMPTY = {kind: 0 for kind in _BLOCK_KINDS}


@dataclass(frozen=True)
class TokenChange:
    delta: float | None
    pct: float | None


@dataclass(frozen=True)
class LocalStatsView:
    """Local accepts expressed as **token amounts** (halving-aware), not raw blocks."""

    previous_day_label: str
    previous_week_label: str
    reward_summary: str
    reward_era: str
    today_xnm: float
    today_xuni: float
    today_xblk: float
    today_blocks_xnm: int
    today_blocks_xuni: int
    today_blocks_xblk: int
    xnm_day: TokenChange
    xuni_day: TokenChange
    xblk_day: TokenChange
    xnm_week: TokenChange
    xuni_week: TokenChange
    xblk_week: TokenChange


def _norm_kind(kind: str) -> str:
    upper = (kind or "").upper()
    if upper in _BLOCK_KINDS:
        return upper
    return "XBLK"


def _pct_change(delta: float | None, baseline: float | None) -> float | None:
    if delta is None or baseline is None or baseline == 0:
        return None
    return (delta / baseline) * 100.0


def _token_change(current: float, baseline: float | None) -> TokenChange:
    if baseline is None:
        return TokenChange(delta=None, pct=None)
    delta = current - baseline
    return TokenChange(delta=delta, pct=_pct_change(delta, baseline))


class LocalMiningStatsTracker:
    """Persist locally accepted **block** counts per mining day; views use token rewards."""

    def __init__(self, history_path: Path) -> None:
        self.history_path = history_path
        self._lock = threading.Lock()
        self._days: dict[str, dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        if not self.history_path.is_file():
            return
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
            days = raw.get("days", {})
            if isinstance(days, dict):
                self._days = {
                    str(day): {
                        kind: int(values.get(kind, 0))
                        for kind in _BLOCK_KINDS
                    }
                    for day, values in days.items()
                    if isinstance(values, dict)
                }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            self._days = {}

    def _save(self) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(
            json.dumps({"days": self._days}, indent=2),
            encoding="utf-8",
        )

    def _period_key(self, now: datetime | None = None) -> str:
        return mining_day(now).isoformat()

    def _today_counts(self, now: datetime | None = None) -> dict[str, int]:
        key = self._period_key(now)
        if key not in self._days:
            self._days[key] = dict(_EMPTY)
        return self._days[key]

    def today_counts(self, now: datetime | None = None) -> dict[str, int]:
        """Raw accepted **block** counts for the current mining day."""
        with self._lock:
            return dict(self._day_counts(mining_day(now)))

    def record_accept(self, kind: str, *, now: datetime | None = None) -> None:
        norm = _norm_kind(kind)
        with self._lock:
            counts = self._today_counts(now)
            counts[norm] = counts.get(norm, 0) + 1
            self._save()

    def _day_counts(self, day: date) -> dict[str, int]:
        raw = self._days.get(day.isoformat())
        if not raw:
            return dict(_EMPTY)
        return {kind: int(raw.get(kind, 0)) for kind in _BLOCK_KINDS}

    def _has_day(self, day: date) -> bool:
        return day.isoformat() in self._days

    def _day_tokens(self, day: date) -> dict[str, float]:
        """Convert that day's block counts to token amounts using that day's reward era."""
        blocks = self._day_counts(day)
        return {
            kind: blocks_to_tokens(kind, blocks[kind], day) for kind in _BLOCK_KINDS
        }

    def _sum_tokens(self, days: list[date]) -> dict[str, float]:
        totals = {kind: 0.0 for kind in _BLOCK_KINDS}
        for day in days:
            daily = self._day_tokens(day)
            for kind in _BLOCK_KINDS:
                totals[kind] += daily[kind]
        return totals

    def _has_any_day(self, days: list[date]) -> bool:
        return any(self._has_day(day) for day in days)

    def view(self, *, now: datetime | None = None) -> LocalStatsView:
        now = now or datetime.now()
        today = mining_day(now)
        yesterday = previous_mining_day(now)
        this_week_days = mining_week_days(now)
        prior_week_days = previous_mining_week_days(now)
        prior_week_start = previous_mining_week_start(now)

        with self._lock:
            today_blocks = self._day_counts(today)
            today_tokens = self._day_tokens(today)
            has_yesterday = self._has_day(yesterday)
            yesterday_tokens = self._day_tokens(yesterday) if has_yesterday else None
            this_week = self._sum_tokens(this_week_days)
            has_prior_week = self._has_any_day(prior_week_days)
            prior_week = self._sum_tokens(prior_week_days) if has_prior_week else None

        def day_change(kind: str) -> TokenChange:
            if yesterday_tokens is None:
                return TokenChange(delta=None, pct=None)
            return _token_change(today_tokens[kind], yesterday_tokens[kind])

        def week_change(kind: str) -> TokenChange:
            if prior_week is None:
                return TokenChange(delta=None, pct=None)
            return _token_change(this_week[kind], prior_week[kind])

        return LocalStatsView(
            previous_day_label=format_day_label(yesterday),
            previous_week_label=format_week_label(prior_week_start),
            reward_summary=current_reward_summary(today),
            reward_era=reward_era_label(today),
            today_xnm=today_tokens["XNM"],
            today_xuni=today_tokens["XUNI"],
            today_xblk=today_tokens["XBLK"],
            today_blocks_xnm=today_blocks["XNM"],
            today_blocks_xuni=today_blocks["XUNI"],
            today_blocks_xblk=today_blocks["XBLK"],
            xnm_day=day_change("XNM"),
            xuni_day=day_change("XUNI"),
            xblk_day=day_change("XBLK"),
            xnm_week=week_change("XNM"),
            xuni_week=week_change("XUNI"),
            xblk_week=week_change("XBLK"),
        )
