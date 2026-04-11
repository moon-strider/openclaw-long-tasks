from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    state_dir: Path = Path.home() / ".openclaw" / "tasks"
    db_name: str = "long-tasks.sqlite"
    scheduler_interval_seconds: int = 15
    lease_ttl_seconds: int = 600
    lease_refresh_seconds: int = 60

    @property
    def db_path(self) -> Path:
        return self.state_dir / self.db_name

    @property
    def artifacts_dir(self) -> Path:
        return self.state_dir / "artifacts"
