from __future__ import annotations

import sqlite3
from pathlib import Path

from core.models import BlockHit


class XenblocksDbWatcher:
    """Read blocks found by xenblocks.exe and route through our submit pipeline."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._seen: set[int] = set()
        self._load_seen()

    def _load_seen(self) -> None:
        if not self.db_path.exists():
            return
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT id FROM processed_blocks").fetchall()
            self._seen.update(r[0] for r in rows)

    def poll_new_hits(self) -> list[BlockHit]:
        if not self.db_path.exists():
            return []
        hits: list[BlockHit] = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, block_hash, block_type, private_key
                FROM unprocessed_blocks
                ORDER BY id
                """
            ).fetchall()
            for row in rows:
                row_id = int(row["id"])
                if row_id in self._seen:
                    continue
                block_type = str(row["block_type"] or "").replace("\x00", "").strip() or "NORMAL"
                hits.append(
                    BlockHit(
                        key=str(row["private_key"]),
                        hash_str=str(row["block_hash"]),
                        block_type=block_type if block_type else "UNKNOWN",
                        attempts=1,
                        strategy="xenblocks_gpu",
                    )
                )
                self._seen.add(row_id)
        return hits