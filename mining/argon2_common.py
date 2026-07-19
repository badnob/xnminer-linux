from __future__ import annotations

import re

from argon2 import low_level
from argon2.low_level import Type

from core.models import BlockHit
from mining.block_types import classify_block

_ARGON2_MEMORY_COST_RE = re.compile(r"\$m=(\d+),")


def is_argon2_encoded(hash_str: str) -> bool:
    return hash_str.startswith("$argon2")


def memory_cost_from_hash(hash_str: str) -> int | None:
    """Read Argon2 m= from an encoded hash string."""
    if not is_argon2_encoded(hash_str):
        return None
    match = _ARGON2_MEMORY_COST_RE.search(hash_str)
    if not match:
        return None
    return int(match.group(1))


def argon2_hash(
    key_text: str,
    salt_hex: str,
    memory_cost: int,
    time_cost: int = 1,
    parallelism: int = 1,
    hash_len: int = 64,
) -> str:
    salt = bytes.fromhex(salt_hex)
    digest = low_level.hash_secret(
        secret=key_text.encode("utf-8"),
        salt=salt,
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        hash_len=hash_len,
        type=Type.ID,
    )
    return digest.decode("ascii")


def classify_hash(hash_str: str) -> str | None:
    kind = classify_block(hash_str)
    return None if kind == "OTHER" else kind


def prepare_hit_for_submit(
    hit: BlockHit,
    *,
    salt_hex: str,
    memory_cost: int,
    time_cost: int = 1,
    parallelism: int = 1,
    hash_len: int = 64,
) -> BlockHit | None:
    """Match official miner: CUDA returns base64 raw hash; pool needs Argon2 encoded string."""
    if is_argon2_encoded(hit.hash_str):
        block_m = memory_cost_from_hash(hit.hash_str)
        if block_m is not None and block_m != memory_cost:
            return None
        return hit

    encoded = argon2_hash(
        hit.key,
        salt_hex,
        memory_cost=memory_cost,
        time_cost=time_cost,
        parallelism=parallelism,
        hash_len=hash_len,
    )
    if hit.hash_str not in encoded:
        return None

    kind = classify_block(encoded, hit.block_type)
    return BlockHit(
        key=hit.key,
        hash_str=encoded,
        block_type=kind,
        attempts=hit.attempts,
        strategy=hit.strategy,
        hps=hit.hps,
        found_at=hit.found_at,
    )


def verify_known_block(salt_hex: str, memory_cost: int = 100) -> bool:
    """Known log0.txt block — valid at m=100."""
    known_key = "b8abe40477f8df07e6e45dc6c32f22d241aea360bd786c544ae11e4accd05e1e"
    h = argon2_hash(known_key, salt_hex, memory_cost=memory_cost)
    return "XEN11" in h