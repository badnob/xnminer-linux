from __future__ import annotations


def submit_accepted(status: int, body: str) -> bool:
    """True when the pool has recorded the block (matches native C++ miner)."""
    if 200 <= status < 300:
        return True
    lowered = body.lower()
    if "already exists" in lowered:
        return True
    return False


def is_difficulty_mismatch(status: int, body: str) -> bool:
    """True when the pool rejected due to memory_cost / difficulty mismatch.

    These are not permanent rejects — the block should be held and submitted
    again when network difficulty matches the hash's m= parameter.
    """
    if status == 0:
        return False
    lowered = body.lower()
    if "hash does not contain 'm=" in lowered:
        return True
    if "does not contain" in lowered and "m=" in lowered:
        return True
    if "memory_cost" in lowered and "does not contain" in lowered:
        return True
    return False


def is_xuni_window_reject(status: int, body: str) -> bool:
    """True when XUNI was submitted outside the accepted time window."""
    if status == 0:
        return False
    lowered = body.lower()
    return (
        "outside of time window" in lowered
        or "outside of proper time frame" in lowered
        or "time frame" in lowered
        or "time window" in lowered
    )


def is_transient_submit_failure(status: int, body: str) -> bool:
    """Network / timeout style failures that should not count as rejects."""
    if status == 0:
        return True
    lowered = body.lower()
    return (
        "timed out" in lowered
        or "timeout" in lowered
        or "urlopen error" in lowered
        or "connection" in lowered and "reset" in lowered
        or "forcibly closed" in lowered
    )


def counts_as_reject(status: int, body: str) -> bool:
    """True only for genuine pool rejects (not difficulty wait / window / network)."""
    if submit_accepted(status, body):
        return False
    if is_difficulty_mismatch(status, body):
        return False
    if is_xuni_window_reject(status, body):
        return False
    if is_transient_submit_failure(status, body):
        return False
    return True


def submit_response_hint(status: int, body: str) -> str:
    """Short human-readable reason for logs."""
    if 200 <= status < 300:
        return f"HTTP {status}"
    lowered = body.lower()
    if "already exists" in lowered:
        return "already on server (duplicate)"
    if is_difficulty_mismatch(status, body):
        return "difficulty mismatch — hold for matching m="
    if is_xuni_window_reject(status, body):
        return "outside XUNI window"
    if is_transient_submit_failure(status, body):
        snippet = body.strip().replace("\n", " ")[:60]
        return f"network error" + (f" — {snippet}" if snippet else "")
    snippet = body.strip().replace("\n", " ")[:80]
    return f"HTTP {status}" + (f" — {snippet}" if snippet else "")
