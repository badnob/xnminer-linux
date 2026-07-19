from __future__ import annotations

import re

XUNI_RE = re.compile(r"XUNI[0-9]")

# Superblock detection — match the official open-source miner, not the docs alone.
#
# Native miner (main.cpp / HashApiMatching.cpp) counts uppercase on
# `hashed_pure` (the Argon2 digest / GPU match string that contains XEN11),
# with threshold capitalCount >= 50.
#
# Docs say "65+ uppercase in 136 characters". Counting 65 on the *full*
# `$argon2id$...$salt$digest` string never fires in practice (salt alone adds
# fixed uppercase and the bar becomes unreachable). Counting 50 on the
# digest segment matches the C++ miner and aligns with observed wallet XBLK
# credits (~1 per few thousand accepts).
SUPER_UPPERCASE_MIN = 50
SUPER_UPPERCASE_DOCS_MIN = 65  # reference only


def hash_digest_for_superblock(hash_str: str) -> str:
    """Return the string the official miner counts uppercase on.

    For full Argon2 encoded hashes, that is the final base64 digest after the
    last '$'. For already-pure digests (GPU match snippets), return as-is.
    """
    if not hash_str:
        return ""
    # Encoded: $argon2id$v=19$m=...,t=1,p=1$salt$digest
    if hash_str.startswith("$") or hash_str.count("$") >= 4:
        return hash_str.rsplit("$", 1)[-1]
    return hash_str


def uppercase_count(text: str) -> int:
    return sum(1 for ch in text if ch.isupper())


def is_superblock(hash_str: str, *, min_upper: int | None = None) -> bool:
    """True when digest has enough uppercase letters (default: official 50)."""
    threshold = SUPER_UPPERCASE_MIN if min_upper is None else min_upper
    digest = hash_digest_for_superblock(hash_str)
    return uppercase_count(digest) >= threshold


def classify_block(hash_str: str, block_type: str = "") -> str:
    """Return XUNI, XBLK (superblock), or XNM (normal XEN11).

    Priority: XUNI marker > superblock (XEN11 + uppercase) > normal XEN11.
    A stale XBLK label without enough uppercase is treated as XNM.
    """
    bt = (block_type or "").upper().replace("\x00", "").strip()
    if bt == "XUNI" or XUNI_RE.search(hash_str):
        return "XUNI"
    if "XEN11" in hash_str and is_superblock(hash_str):
        return "XBLK"
    if "XEN11" in hash_str or bt in ("XEN11", "NORMAL", "XNM", "XBLK"):
        return "XNM"
    return "OTHER"
