"""Restore queued blocks from queue.jsonl (and failed submissions)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from block_queue.store import BlockStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "blocks.db")
    parser.add_argument("--jsonl", type=Path, default=ROOT / "data" / "queue.jsonl")
    parser.add_argument(
        "--rejected-jsonl", type=Path, default=ROOT / "data" / "rejected.jsonl"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = BlockStore(args.db, args.jsonl, args.rejected_jsonl)
    before = store.pending_count()
    print(f"pending before: {before}")

    imported = store.import_pending_from_jsonl(dry_run=args.dry_run)
    print(f"imported: {imported}")
    print(f"pending after: {store.pending_count() if not args.dry_run else before + imported}")
    if args.dry_run:
        print("(dry run — no changes written)")


if __name__ == "__main__":
    main()