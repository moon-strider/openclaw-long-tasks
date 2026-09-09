from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def stable_json(data: Any) -> str:
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def waiting_alert_hash(prompt: str | None, task_id: str, step_index: int) -> str:
    payload = stable_json({"prompt": prompt or "", "task_id": task_id, "step_index": step_index})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
