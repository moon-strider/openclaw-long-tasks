#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from long_tasks.config import Settings

DB = Settings().db_path


def main() -> int:
    if not DB.exists():
        print(json.dumps({"ok": False, "error": f"missing db: {DB}"}, ensure_ascii=False))
        return 1
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    payload = {"ok": True, "db": str(DB), "tables": tables, "rows": {}}
    for table in tables:
        payload["rows"][table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 10")]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
