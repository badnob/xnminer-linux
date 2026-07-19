from __future__ import annotations

import secrets


def make_random_key_fn(lane_id: int):
    def key_fn(index: int) -> str:
        return secrets.token_hex(32)

    return key_fn