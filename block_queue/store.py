from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from core.models import BlockHit
from mining.block_types import classify_block

RESUBMISSION_REASON = "resubmission"
OUTSIDE_XUNI_WINDOW_REASON = "outside_xuni_window"
SHUTDOWN_PENDING_REASON = "shutdown_pending"
DIFFICULTY_CHANGE_REASON = "difficulty_change"


class BlockStore:
    def __init__(
        self,
        db_path: Path,
        jsonl_path: Path,
        rejected_jsonl_path: Path | None = None,
    ) -> None:
        self.db_path = db_path
        self.jsonl_path = jsonl_path
        self.rejected_jsonl_path = rejected_jsonl_path or (
            jsonl_path.parent / "rejected.jsonl"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self.rejected_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    hash_str TEXT NOT NULL,
                    block_type TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    hps REAL NOT NULL,
                    found_at TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    queue_reason TEXT NOT NULL DEFAULT '',
                    reject_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending'
                )
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(pending_blocks)")
            }
            if "queue_reason" not in columns:
                conn.execute(
                    "ALTER TABLE pending_blocks ADD COLUMN queue_reason TEXT NOT NULL DEFAULT ''"
                )
            if "reject_count" not in columns:
                conn.execute(
                    "ALTER TABLE pending_blocks ADD COLUMN reject_count INTEGER NOT NULL DEFAULT 0"
                )
            if "memory_cost" not in columns:
                conn.execute(
                    "ALTER TABLE pending_blocks ADD COLUMN memory_cost INTEGER"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS submitted_blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    hash_str TEXT NOT NULL,
                    block_type TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    http_status INTEGER,
                    response_body TEXT,
                    submitted_at TEXT NOT NULL
                )
                """
            )

    def enqueue(self, hit: BlockHit, reason: str) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        record = {
            "key": hit.key,
            "hash_str": hit.hash_str,
            "block_type": hit.block_type,
            "strategy": hit.strategy,
            "attempts": hit.attempts,
            "hps": hit.hps,
            "found_at": hit.found_at.isoformat(timespec="seconds"),
            "queued_at": now,
            "reason": reason,
            "memory_cost": hit.memory_cost,
        }
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO pending_blocks
                (key, hash_str, block_type, strategy, attempts, hps, found_at, queued_at,
                 queue_reason, memory_cost)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hit.key,
                    hit.hash_str,
                    hit.block_type,
                    hit.strategy,
                    hit.attempts,
                    hit.hps,
                    hit.found_at.isoformat(timespec="seconds"),
                    now,
                    reason,
                    hit.memory_cost,
                ),
            )
            return int(cur.lastrowid or 0)

    def log_rejection(
        self,
        hit: BlockHit,
        http_status: int,
        body: str,
        source: str,
    ) -> None:
        """Append a rejected submission to rejected.jsonl."""
        record = {
            "key": hit.key,
            "hash_str": hit.hash_str,
            "block_type": hit.block_type,
            "strategy": hit.strategy,
            "attempts": hit.attempts,
            "hps": hit.hps,
            "found_at": hit.found_at.isoformat(timespec="seconds"),
            "rejected_at": datetime.now().isoformat(timespec="seconds"),
            "http_status": http_status,
            "response_body": body,
            "source": source,
            "category": RESUBMISSION_REASON,
        }
        with self.rejected_jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def mark_pending_reason(self, pending_id: int, reason: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE pending_blocks
                SET queue_reason = ?
                WHERE id = ? AND status = 'pending'
                """,
                (reason, pending_id),
            )

    def record_rejection(
        self,
        hit: BlockHit,
        http_status: int,
        body: str,
        source: str,
        *,
        pending_id: int | None = None,
        queue_reason: str | None = None,
    ) -> bool:
        """
        Log rejection, record attempt, and ensure block is in resubmission queue.

        Returns True if the block was newly added to the pending queue.
        """
        if pending_id is not None:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT reject_count FROM pending_blocks WHERE id = ? AND status = 'pending'",
                    (pending_id,),
                ).fetchone()
                if not row:
                    return False
                first_reject = int(row[0]) == 0
                conn.execute(
                    """
                    UPDATE pending_blocks
                    SET queue_reason = ?, reject_count = reject_count + 1
                    WHERE id = ? AND status = 'pending'
                    """,
                    (queue_reason or RESUBMISSION_REASON, pending_id),
                )
            if first_reject:
                self.log_rejection(hit, http_status, body, source)
                self.record_direct_submit(hit, http_status, body)
            return False

        existing: tuple[int, bool] | None = None
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, reject_count FROM pending_blocks
                WHERE key = ? AND status = 'pending'
                """,
                (hit.key,),
            ).fetchone()
            if row:
                first_reject = int(row[1]) == 0
                conn.execute(
                    """
                    UPDATE pending_blocks
                    SET queue_reason = ?, reject_count = reject_count + 1
                    WHERE id = ?
                    """,
                    (RESUBMISSION_REASON, row[0]),
                )
                existing = (int(row[0]), first_reject)

        if existing:
            _, first_reject = existing
            if first_reject:
                self.log_rejection(hit, http_status, body, source)
                self.record_direct_submit(hit, http_status, body)
            return False

        self.log_rejection(hit, http_status, body, source)
        self.record_direct_submit(hit, http_status, body)

        self.enqueue(hit, RESUBMISSION_REASON)
        return True

    def accepted_keys(self) -> set[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT key FROM submitted_blocks
                WHERE http_status >= 200 AND http_status < 300
                """
            ).fetchall()
        return {row[0] for row in rows}

    def list_accepted_submits(self, limit: int = 5000) -> list[dict]:
        """Accepted submits for xenblockscan backfill (newest first, unique key)."""
        limit = max(1, min(int(limit), 20000))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT key, hash_str, block_type, submitted_at
                FROM submitted_blocks
                WHERE http_status >= 200 AND http_status < 300
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        seen: set[str] = set()
        out: list[dict] = []
        for key, hash_str, block_type, submitted_at in rows:
            if not key or key in seen:
                continue
            seen.add(key)
            kind = classify_block(hash_str or "", block_type or "")
            if kind == "OTHER":
                kind = (block_type or "XNM").upper()
            out.append(
                {
                    "key": key,
                    "hash_str": hash_str or "",
                    "block_type": kind,
                    "submitted_at": submitted_at,
                }
            )
        return out

    def reclassify_pending(self) -> int:
        """Fix pending block_type labels using current classify_block rules."""
        updated = 0
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, hash_str, block_type
                FROM pending_blocks
                WHERE status = 'pending'
                """
            ).fetchall()
            for row_id, hash_str, old_type in rows:
                new_type = classify_block(hash_str, old_type or "")
                if new_type in ("OTHER", old_type):
                    continue
                conn.execute(
                    "UPDATE pending_blocks SET block_type = ? WHERE id = ?",
                    (new_type, row_id),
                )
                updated += 1
        return updated

    def migrate_rejected_pending(self) -> int:
        """Mark pending blocks as resubmission when they already have failed submissions."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT pb.id
                FROM pending_blocks pb
                JOIN submitted_blocks sb ON sb.key = pb.key
                WHERE pb.status = 'pending'
                  AND pb.queue_reason != ?
                  AND (sb.http_status < 200 OR sb.http_status >= 300)
                """,
                (RESUBMISSION_REASON,),
            ).fetchall()
            for (row_id,) in rows:
                conn.execute(
                    """
                    UPDATE pending_blocks
                    SET queue_reason = ?
                    WHERE id = ?
                    """,
                    (RESUBMISSION_REASON, row_id),
                )
            return len(rows)

    def import_pending_from_jsonl(self, *, dry_run: bool = False) -> int:
        """Re-import queued blocks from the jsonl log that are not yet accepted."""
        if not self.jsonl_path.exists():
            return 0

        accepted = self.accepted_keys()
        with sqlite3.connect(self.db_path) as conn:
            pending = {
                row[0]
                for row in conn.execute(
                    "SELECT key FROM pending_blocks WHERE status='pending'"
                ).fetchall()
            }
            imported = 0
            seen: set[str] = set()
            with self.jsonl_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    key = record["key"]
                    if key in seen or key in pending or key in accepted:
                        continue
                    seen.add(key)
                    if dry_run:
                        imported += 1
                        continue
                    conn.execute(
                        """
                        INSERT INTO pending_blocks
                        (key, hash_str, block_type, strategy, attempts, hps,
                         found_at, queued_at, queue_reason, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                        """,
                        (
                            key,
                            record["hash_str"],
                            record["block_type"],
                            record["strategy"],
                            int(record["attempts"]),
                            float(record.get("hps", 0.0)),
                            record.get("found_at", record.get("queued_at")),
                            record.get("queued_at"),
                            record.get("reason", "imported"),
                        ),
                    )
                    pending.add(key)
                    imported += 1
            if not dry_run:
                conn.commit()
        return imported

    def pending_count(self, *, resubmission: bool | None = None) -> int:
        return sum(self.pending_by_type(resubmission=resubmission).values())

    def pending_by_type(self, *, resubmission: bool | None = None) -> dict[str, int]:
        clause = ""
        if resubmission is True:
            clause = f"AND queue_reason = '{RESUBMISSION_REASON}'"
        elif resubmission is False:
            clause = f"AND queue_reason != '{RESUBMISSION_REASON}'"

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT block_type, COUNT(*)
                FROM pending_blocks
                WHERE status='pending' {clause}
                GROUP BY block_type
                """
            ).fetchall()
        counts = {"XUNI": 0, "XNM": 0, "XBLK": 0}
        for block_type, n in rows:
            key = (block_type or "").upper()
            if key in counts:
                counts[key] = int(n)
            else:
                counts["XBLK"] += int(n)
        return counts

    def pending(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pending_blocks WHERE status='pending' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_submitted(self, row_id: int, http_status: int, body: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM pending_blocks WHERE id=?", (row_id,)
            ).fetchone()
            if not row:
                return
            conn.execute(
                "UPDATE pending_blocks SET status='submitted' WHERE id=?", (row_id,)
            )
            conn.execute(
                """
                INSERT INTO submitted_blocks
                (key, hash_str, block_type, strategy, attempts, http_status, response_body, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    http_status,
                    body,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def record_direct_submit(self, hit: BlockHit, http_status: int, body: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO submitted_blocks
                (key, hash_str, block_type, strategy, attempts, http_status, response_body, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hit.key,
                    hit.hash_str,
                    hit.block_type,
                    hit.strategy,
                    hit.attempts,
                    http_status,
                    body,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )