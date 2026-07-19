"""Mining day/week boundaries used by local accepts and wallet balances.

Day:  01:00 → next day 00:59 (rolls at 1am local time)
Week: Monday 01:00 → next Monday 00:59
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

DAY_ROLLOVER_HOUR = 1  # local 1:00 AM


def mining_day(now: datetime | None = None) -> date:
    """Return the mining-day date key for `now` (1am boundary)."""
    now = now or datetime.now()
    if now.hour < DAY_ROLLOVER_HOUR:
        return now.date() - timedelta(days=1)
    return now.date()


def previous_mining_day(now: datetime | None = None) -> date:
    return mining_day(now) - timedelta(days=1)


def mining_week_start(now: datetime | None = None) -> date:
    """Monday date of the mining week that contains `now` (week starts Mon 1am)."""
    day = mining_day(now)
    return day - timedelta(days=day.weekday())  # Monday=0


def previous_mining_week_start(now: datetime | None = None) -> date:
    return mining_week_start(now) - timedelta(days=7)


def mining_week_days(now: datetime | None = None) -> list[date]:
    """Mining days in the current week from Monday through current mining day."""
    start = mining_week_start(now)
    end = mining_day(now)
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def previous_mining_week_days(now: datetime | None = None) -> list[date]:
    """All seven mining days of the previous Mon–Sun mining week."""
    start = previous_mining_week_start(now)
    return [start + timedelta(days=offset) for offset in range(7)]


def format_day_label(day: date) -> str:
    return day.strftime("%b %d")


def format_week_label(week_start: date) -> str:
    week_end = week_start + timedelta(days=6)
    return f"{format_day_label(week_start)}-{format_day_label(week_end)}"
