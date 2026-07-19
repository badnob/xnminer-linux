from __future__ import annotations

import hashlib


def make_fibonacci_key_fn(lane_id: int):
    a, b = lane_id, lane_id + 1

    def key_fn(index: int) -> str:
        nonlocal a, b
        a, b = b, (a + b) % (2**256)
        return hashlib.sha256(f"fib:{a}:{index}".encode()).hexdigest()

    return key_fn