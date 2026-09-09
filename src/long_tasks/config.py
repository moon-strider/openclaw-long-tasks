from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Settings:
    state_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("LONG_TASKS_STATE_DIR", Path.home() / ".openclaw" / "tasks")
        )
    )
    db_name: str = "long-tasks.sqlite"
    scheduler_interval_seconds: int = 15
    lease_ttl_seconds: int = 600
    lease_refresh_seconds: int = 60

    def __post_init__(self):
        self.state_dir = Path(self.state_dir).expanduser().resolve()
        if Path(self.db_name).name != self.db_name or self.db_name in {"", ".", ".."}:
            raise ValueError("db_name must be a filename")
        if (
            not 0 < self.lease_refresh_seconds < self.lease_ttl_seconds
            or self.scheduler_interval_seconds <= 0
        ):
            raise ValueError("Use positive intervals and refresh the lease before its expiry")

    @property
    def db_path(self) -> Path:
        return self.state_dir / self.db_name

    @property
    def artifacts_dir(self) -> Path:
        return self.state_dir / "artifacts"
