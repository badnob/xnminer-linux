"""Dev helper: inspect a local xenblocks blocks.db schema.

Usage:
  python debug/_inspect_xenblocks_db.py [path\\to\\blocks.db]
"""
import sqlite3
import sys
from pathlib import Path

db = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("blocks.db")
if not db.is_file():
    raise SystemExit(f"Database not found: {db}")
c = sqlite3.connect(db)
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables", tables)
for t in tables:
    cols = [d[1] for d in c.execute(f"PRAGMA table_info({t})")]
    print(t, cols)
    row = c.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 1").fetchone()
    print("  latest", row)