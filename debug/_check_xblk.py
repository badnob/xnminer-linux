"""One-off: inspect XBLK classification vs submitted/pending blocks."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mining.block_types import SUPER_UPPERCASE_MIN, classify_block, is_superblock

db = Path("data/blocks.db")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

print("tables:", [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")])

for table in ("submitted_blocks", "pending_blocks"):
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error as e:
        print(table, "missing", e)
        continue
    print(f"\n=== {table} count={n} ===")
    rows = conn.execute(
        f"SELECT block_type, COUNT(*) AS c FROM {table} GROUP BY block_type"
    ).fetchall()
    for r in rows:
        print(f"  labeled {r['block_type']!r}: {r['c']}")

    # Reclassify from hash for submitted/pending
    sample = conn.execute(
        f"SELECT key, hash_str, block_type FROM {table} LIMIT 50000"
    ).fetchall()
    true_super = 0
    relabel = {}
    upper_hist = []
    for r in sample:
        h = r["hash_str"] or ""
        true = classify_block(h, "")
        relabel[true] = relabel.get(true, 0) + 1
        if true == "XBLK":
            true_super += 1
            upper = sum(1 for ch in h if ch.isupper())
            upper_hist.append(upper)
            if true_super <= 5:
                print(
                    f"  TRUE XBLK key={r['key'][:16]}... labeled={r['block_type']} "
                    f"upper={upper} len={len(h)}"
                )
    print(f"  reclassified from hash: {relabel}")
    print(f"  true XBLK among scanned: {true_super}")
    if upper_hist:
        print(f"  true XBLK upper min/max: {min(upper_hist)}/{max(upper_hist)}")

# Uppercase distribution among accepted XEN11 hashes (submitted ok)
print("\n=== uppercase distribution on submitted XEN11-like hashes ===")
rows = conn.execute(
    """
    SELECT hash_str, block_type, http_status FROM submitted_blocks
    WHERE http_status >= 200 AND http_status < 300
    """
).fetchall()
buckets = {f"<{SUPER_UPPERCASE_MIN}": 0, f">={SUPER_UPPERCASE_MIN}": 0}
near = 0
max_upper = 0
examples_near = []
for r in rows:
    h = r["hash_str"] or ""
    if "XEN11" not in h:
        continue
    upper = sum(1 for ch in h if ch.isupper())
    max_upper = max(max_upper, upper)
    if upper >= SUPER_UPPERCASE_MIN:
        buckets[f">={SUPER_UPPERCASE_MIN}"] += 1
    else:
        buckets[f"<{SUPER_UPPERCASE_MIN}"] += 1
    if upper >= SUPER_UPPERCASE_MIN - 5:
        near += 1
        if len(examples_near) < 8:
            examples_near.append((upper, r["block_type"], r["key"][:16] if "key" in r.keys() else "?"))

# need key in select
rows2 = conn.execute(
    """
    SELECT key, hash_str, block_type, http_status FROM submitted_blocks
    WHERE http_status >= 200 AND http_status < 300
    """
).fetchall()
buckets = {f"<{SUPER_UPPERCASE_MIN}": 0, f">={SUPER_UPPERCASE_MIN}": 0}
max_upper = 0
near_examples = []
true_xblk_accepted = 0
for r in rows2:
    h = r["hash_str"] or ""
    if "XEN11" not in h:
        continue
    upper = sum(1 for ch in h if ch.isupper())
    max_upper = max(max_upper, upper)
    if upper >= SUPER_UPPERCASE_MIN:
        buckets[f">={SUPER_UPPERCASE_MIN}"] += 1
        true_xblk_accepted += 1
    else:
        buckets[f"<{SUPER_UPPERCASE_MIN}"] += 1
    if upper >= SUPER_UPPERCASE_MIN - 5 and len(near_examples) < 10:
        near_examples.append((upper, r["block_type"], r["key"][:16]))

print("accepted with XEN11:", sum(buckets.values()))
print("uppercase buckets:", buckets)
print("max uppercase seen:", max_upper)
print("true superblocks accepted (by hash rule):", true_xblk_accepted)
print("near-miss examples (upper >= 60):", near_examples)

print("\nthreshold used by this miner:", SUPER_UPPERCASE_MIN)
print("native HashApiMatching.cpp uses 50 (looser, causes false XBLK labels)")
