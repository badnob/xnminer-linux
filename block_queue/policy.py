"""When queued blocks are allowed to submit."""

from __future__ import annotations

from datetime import datetime

XUNI_WINDOW_LABEL = ":56-:59, :00-:04"


def in_xuni_window(now: datetime | None = None) -> bool:
    """Pool accepts XUNI during :56-:59 and :00-:04 each hour."""
    now = now or datetime.now()
    return now.minute >= 56 or now.minute < 5


def ready_to_flush(block_type: str, *, now: datetime | None = None) -> tuple[bool, str]:
    """
    Flush runs only when the network is up (caller checks that first).

    - XUNI: submit only inside the XUNI window; otherwise keep queued for next start.
    - XNM / XBLK: submit any time; if submit fails on shutdown, keep queued for next start.
    """
    kind = (block_type or "").upper()
    if kind == "XUNI" and not in_xuni_window(now):
        return False, "waiting_for_xuni_window"
    return True, "ready"