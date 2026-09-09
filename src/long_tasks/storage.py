from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from .config import Settings
from .models import (
    AttemptStatus,
    Step,
    StepKind,
    StepStatus,
    Task,
    TaskStatus,
    ensure_step_transition,
    ensure_task_transition,
)
from .utils import ensure_parent, new_id, stable_json, utcnow

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_session_key TEXT NOT NULL,
    source_channel TEXT NOT NULL,
    source_chat_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    goal TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    retry_budget INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_summary TEXT,
    waiting_prompt TEXT,
    waiting_alert_hash TEXT,
    waiting_alert_sent_at TEXT,
    notify_channel TEXT NOT NULL,
    notify_chat_id TEXT NOT NULL,
    notify_message_ref TEXT,
    reply_message_id TEXT,
    final_report_max_chars INTEGER NOT NULL DEFAULT 4000,
    shared_state_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    instructions TEXT NOT NULL,
    verification_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    result_summary TEXT,
    artifact_paths_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    UNIQUE(task_id, step_index)
);

CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    role TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    output_json TEXT,
    error_text TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY(step_id) REFERENCES steps(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_id TEXT,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY(step_id) REFERENCES steps(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    event_id TEXT,
    channel TEXT NOT NULL,
    target TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL,
    error_text TEXT,
    created_at TEXT NOT NULL,
    last_attempt_at TEXT,
    sent_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    snapshot_json TEXT NOT NULL,
    claim_token TEXT,
    claim_expires_at TEXT,
    next_attempt_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_next_run_at ON tasks(status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_tasks_lease_expires_at ON tasks(lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_steps_task_id_step_index ON steps(task_id, step_index);
CREATE INDEX IF NOT EXISTS idx_events_task_id_created_at ON events(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_task_id_step_id_started_at ON attempts(task_id, step_id, started_at);
CREATE INDEX IF NOT EXISTS idx_notifications_status_created_at ON notifications(status, created_at);
"""


@dataclass(slots=True)
class LeaseResult:
    task_id: str
    reclaimed_expired_lease: bool = False


class TaskStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        ensure_parent(self.settings.db_path)
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        self.settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.settings.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_task(self, task: Task, steps: list[Step]) -> None:
        from .validation import validate_task

        validate_task(task, steps)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, title, source_session_key, source_channel, source_chat_id,
                    created_at, updated_at, status, goal, plan_json, current_step_index,
                    next_run_at, lease_owner, lease_expires_at, retry_budget, last_error,
                    last_summary, waiting_prompt, waiting_alert_hash, waiting_alert_sent_at,
                    notify_channel, notify_chat_id, notify_message_ref, reply_message_id, final_report_max_chars
                    , shared_state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.title,
                    task.source_session_key,
                    task.source_channel,
                    task.source_chat_id,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    task.status.value,
                    task.goal,
                    stable_json(task.plan),
                    task.current_step_index,
                    task.next_run_at.isoformat(),
                    task.lease_owner,
                    task.lease_expires_at.isoformat() if task.lease_expires_at else None,
                    task.retry_budget,
                    task.last_error,
                    task.last_summary,
                    task.waiting_prompt,
                    task.waiting_alert_hash,
                    task.waiting_alert_sent_at.isoformat() if task.waiting_alert_sent_at else None,
                    task.notify_channel,
                    task.notify_chat_id,
                    task.notify_message_ref,
                    task.reply_message_id,
                    task.final_report_max_chars,
                    stable_json(task.shared_state),
                ),
            )
            for step in steps:
                conn.execute(
                    """
                    INSERT INTO steps (
                        id, task_id, step_index, title, kind, instructions, verification_json,
                        status, attempt_count, max_attempts, result_summary, artifact_paths_json,
                        started_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        step.id,
                        step.task_id,
                        step.step_index,
                        step.title,
                        step.kind.value,
                        step.instructions,
                        stable_json(step.verification),
                        step.status.value,
                        step.attempt_count,
                        step.max_attempts,
                        step.result_summary,
                        stable_json(step.artifact_paths),
                        step.started_at.isoformat() if step.started_at else None,
                        step.finished_at.isoformat() if step.finished_at else None,
                    ),
                )

    def list_tasks(self) -> list[Task]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY next_run_at, created_at").fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_task(self, task_id: str) -> Task | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def get_steps(self, task_id: str) -> list[Step]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE task_id = ? ORDER BY step_index", (task_id,)
            ).fetchall()
        return [self._row_to_step(row) for row in rows]

    def get_current_step(self, task_id: str) -> Step | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT * FROM steps WHERE task_id = ? AND step_index = ?",
                (task_id, task.current_step_index),
            ).fetchone()
        return self._row_to_step(row) if row else None

    def get_completed_steps(self, task_id: str) -> list[Step]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE task_id = ? AND status = ? ORDER BY step_index",
                (task_id, StepStatus.DONE.value),
            ).fetchall()
        return [self._row_to_step(row) for row in rows]

    def set_task_shared_state(
        self, conn: sqlite3.Connection, task_id: str, state: dict[str, Any]
    ) -> None:
        conn.execute(
            "UPDATE tasks SET shared_state_json = ?, updated_at = ? WHERE id = ?",
            (stable_json(state), utcnow().isoformat(), task_id),
        )

    def list_attempts(self, task_id: str) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn, conn:
            return conn.execute(
                "SELECT * FROM attempts WHERE task_id = ? ORDER BY started_at",
                (task_id,),
            ).fetchall()

    def list_events(self, task_id: str, limit: int = 20) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn, conn:
            return conn.execute(
                "SELECT * FROM events WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()

    def runnable_tasks(self, now=None) -> list[Task]:
        now = now or utcnow()
        now_str = now.isoformat()
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status IN (?, ?)
                  AND next_run_at <= ?
                  AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at < ?)
                ORDER BY next_run_at, created_at LIMIT 100
                """,
                (TaskStatus.READY.value, TaskStatus.RUNNING.value, now_str, now_str),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def acquire_lease(self, task_id: str, worker_id: str, now=None) -> LeaseResult | None:
        now = now or utcnow()
        expiry = now + timedelta(seconds=self.settings.lease_ttl_seconds)
        with self.transaction() as conn:
            current = conn.execute(
                "SELECT lease_owner, lease_expires_at FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if current is None:
                return None
            prior_expired = (
                current["lease_owner"] is not None
                and current["lease_expires_at"] is not None
                and current["lease_expires_at"] < now.isoformat()
            )
            result = conn.execute(
                """
                UPDATE tasks
                SET lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ?
                  AND status IN ('ready', 'running') AND next_run_at <= ?
                  AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)
                """,
                (
                    worker_id,
                    expiry.isoformat(),
                    now.isoformat(),
                    task_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            if result.rowcount != 1:
                return None
            return LeaseResult(task_id=task_id, reclaimed_expired_lease=prior_expired)

    def refresh_lease(self, task_id: str, worker_id: str, now=None) -> bool:
        now = now or utcnow()
        expiry = now + timedelta(seconds=self.settings.lease_ttl_seconds)
        with self.transaction() as conn:
            result = conn.execute(
                """
                UPDATE tasks
                SET lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND lease_owner = ? AND lease_expires_at > ?
                  AND status IN ('ready', 'running')
                """,
                (expiry.isoformat(), now.isoformat(), task_id, worker_id, now.isoformat()),
            )
            return result.rowcount == 1

    def release_lease(self, task_id: str, worker_id: str, now=None) -> bool:
        now = now or utcnow()
        with self.transaction() as conn:
            result = conn.execute(
                """
                UPDATE tasks
                SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE id = ? AND lease_owner = ?
                """,
                (now.isoformat(), task_id, worker_id),
            )
            return result.rowcount == 1

    def transition_task_status(
        self, conn: sqlite3.Connection, task_id: str, target: TaskStatus, **updates: Any
    ) -> None:
        current = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if current is None:
            raise KeyError(task_id)
        current_status = TaskStatus(current["status"])
        ensure_task_transition(current_status, target)
        payload = {
            "status": target.value,
            "updated_at": utcnow().isoformat(),
            **self._normalize_updates(updates),
        }
        self._update_row(conn, "tasks", "id", task_id, payload)

    def transition_step_status(
        self, conn: sqlite3.Connection, step_id: str, target: StepStatus, **updates: Any
    ) -> None:
        current = conn.execute("SELECT status FROM steps WHERE id = ?", (step_id,)).fetchone()
        if current is None:
            raise KeyError(step_id)
        current_status = StepStatus(current["status"])
        ensure_step_transition(current_status, target)
        payload = {"status": target.value, **self._normalize_step_updates(updates)}
        self._update_row(conn, "steps", "id", step_id, payload)

    def record_attempt_start(
        self,
        conn: sqlite3.Connection,
        attempt_id: str,
        task_id: str,
        step_id: str,
        role: str,
        worker_id: str,
        snapshot: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO attempts (id, task_id, step_id, role, worker_id, started_at, status, input_snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                task_id,
                step_id,
                role,
                worker_id,
                utcnow().isoformat(),
                AttemptStatus.RETRY.value,
                stable_json(snapshot),
            ),
        )

    def record_attempt_finish(
        self,
        conn: sqlite3.Connection,
        attempt_id: str,
        status: AttemptStatus,
        output: dict[str, Any] | None = None,
        error_text: str | None = None,
    ) -> None:
        conn.execute(
            """
            UPDATE attempts
            SET finished_at = ?, status = ?, output_json = ?, error_text = ?
            WHERE id = ?
            """,
            (
                utcnow().isoformat(),
                status.value,
                stable_json(output) if output is not None else None,
                error_text,
                attempt_id,
            ),
        )

    def add_event(
        self,
        conn: sqlite3.Connection,
        event_id: str,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        step_id: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO events (id, task_id, step_id, type, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, task_id, step_id, event_type, utcnow().isoformat(), stable_json(payload)),
        )

    def enqueue_notification(
        self,
        conn: sqlite3.Connection,
        notification_id: str,
        task_id: str,
        channel: str,
        target: str,
        message: str,
        event_id: str | None = None,
    ) -> None:
        snapshot = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if snapshot is None:
            raise KeyError(task_id)
        conn.execute(
            """
            INSERT INTO notifications (id, task_id, event_id, channel, target, message, status, created_at, attempt_count, snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?)
            """,
            (
                notification_id,
                task_id,
                event_id,
                channel,
                target,
                message,
                utcnow().isoformat(),
                stable_json(dict(snapshot)),
            ),
        )

    def list_pending_notifications(self, limit: int = 100) -> list[sqlite3.Row]:
        now = utcnow().isoformat()
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM notifications WHERE (status='pending' AND (next_attempt_at IS NULL OR next_attempt_at<=?)) OR (status='sending' AND claim_expires_at<=?) ORDER BY created_at LIMIT ?",
                (now, now, limit),
            ).fetchall()

    def claim_notification(self, notification_id: str, token: str):
        now = utcnow()
        with self.transaction() as conn:
            updated = conn.execute(
                "UPDATE notifications SET status='sending',claim_token=?,claim_expires_at=?,last_attempt_at=?,attempt_count=attempt_count+1 WHERE id=? AND ((status='pending' AND (next_attempt_at IS NULL OR next_attempt_at<=?)) OR (status='sending' AND claim_expires_at<=?))",
                (
                    token,
                    (now + timedelta(minutes=5)).isoformat(),
                    now.isoformat(),
                    notification_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            return (
                conn.execute(
                    "SELECT * FROM notifications WHERE id=?", (notification_id,)
                ).fetchone()
                if updated.rowcount
                else None
            )

    def mark_notification_sent(self, notification_id: str, token: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE notifications SET status='sent',sent_at=?,error_text=NULL,claim_token=NULL,claim_expires_at=NULL WHERE id=? AND claim_token=?",
                (utcnow().isoformat(), notification_id, token),
            )

    def refresh_notification(self, notification_id: str, token: str) -> bool:
        now = utcnow()
        with self.transaction() as conn:
            return (
                conn.execute(
                    "UPDATE notifications SET claim_expires_at=? WHERE id=? AND claim_token=? AND status='sending' AND claim_expires_at>?",
                    (
                        (now + timedelta(minutes=5)).isoformat(),
                        notification_id,
                        token,
                        now.isoformat(),
                    ),
                ).rowcount
                == 1
            )

    def mark_notification_failed(self, notification_id: str, error_text: str, token: str) -> None:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT attempt_count FROM notifications WHERE id=? AND claim_token=?",
                (notification_id, token),
            ).fetchone()
            if row is None:
                return
            retry_at = utcnow() + timedelta(seconds=min(3600, 30 * 2 ** min(row[0], 7)))
            conn.execute(
                "UPDATE notifications SET status=?,error_text=?,next_attempt_at=?,claim_token=NULL,claim_expires_at=NULL WHERE id=? AND claim_token=?",
                (
                    "dead" if row[0] >= 8 else "pending",
                    error_text[:1024],
                    retry_at.isoformat(),
                    notification_id,
                    token,
                ),
            )

    def owns_lease(self, conn, task_id: str, token: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM tasks WHERE id=? AND lease_owner=? AND lease_expires_at>? AND status IN ('ready','running')",
                (task_id, token, utcnow().isoformat()),
            ).fetchone()
            is not None
        )

    def resume_task(self, task_id: str, user_reply: str | None = None) -> Task:
        if user_reply is not None and (not user_reply.strip() or len(user_reply) > 16384):
            raise ValueError("Reply must contain 1–16384 characters")
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if TaskStatus(row["status"]) not in {
                TaskStatus.WAITING_USER,
                TaskStatus.BLOCKED,
                TaskStatus.FAILED,
            }:
                raise ValueError("Task is not waiting, blocked or failed")
            state = json.loads(row["shared_state_json"])
            if user_reply is not None:
                state["user_reply"] = {"prompt": row["waiting_prompt"], "text": user_reply}
            self.transition_task_status(
                conn,
                task_id,
                TaskStatus.READY,
                waiting_prompt=None,
                waiting_alert_hash=None,
                waiting_alert_sent_at=None,
                next_run_at=utcnow(),
                last_error=None,
                lease_owner=None,
                lease_expires_at=None,
                shared_state_json=state,
            )
            # An explicit resume grants a new attempt budget; attempt history is retained.
            conn.execute(
                "UPDATE steps SET status='pending',attempt_count=0,started_at=NULL,finished_at=NULL WHERE task_id=? AND step_index=? AND status!='done'",
                (task_id, row["current_step_index"]),
            )
            self.add_event(
                conn, new_id(), task_id, "task.resumed", {"reply_supplied": user_reply is not None}
            )
        resumed = self.get_task(task_id)
        assert resumed is not None
        return resumed

    def cancel_task(self, task_id: str) -> Task:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            status = TaskStatus(row["status"])
            if status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                raise ValueError(f"Task {task_id} is already terminal")
            self.transition_task_status(
                conn, task_id, TaskStatus.CANCELLED, lease_owner=None, lease_expires_at=None
            )
            conn.execute(
                "UPDATE steps SET status='blocked',finished_at=?,result_summary='task cancelled' WHERE task_id=? AND status='in_progress'",
                (utcnow().isoformat(), task_id),
            )
            conn.execute(
                "UPDATE attempts SET status='failed',finished_at=?,error_text='task cancelled' WHERE task_id=? AND finished_at IS NULL",
                (utcnow().isoformat(), task_id),
            )
            self.add_event(conn, new_id(), task_id, "task.cancelled", {})
        task = self.get_task(task_id)
        assert task is not None
        return task

    def recover_expired_running_tasks(self, now=None) -> list[str]:
        now = now or utcnow()
        recovered: list[str] = []
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT id FROM tasks
                WHERE status = ?
                  AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)
                """,
                (TaskStatus.RUNNING.value, now.isoformat()),
            ).fetchall()
            for row in rows:
                task_id = row["id"]
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, next_run_at = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (TaskStatus.READY.value, now.isoformat(), now.isoformat(), task_id),
                )
                conn.execute(
                    "UPDATE steps SET status='pending',started_at=NULL WHERE task_id=? AND status='in_progress'",
                    (task_id,),
                )
                conn.execute(
                    "UPDATE attempts SET status='retry',finished_at=?,error_text='lease expired before checkpoint' WHERE task_id=? AND finished_at IS NULL",
                    (now.isoformat(), task_id),
                )
                self.add_event(conn, new_id(), task_id, "lease.recovered", {})
                recovered.append(task_id)
        return recovered

    def _normalize_updates(self, updates: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in updates.items():
            if isinstance(value, Path):
                normalized[key] = str(value)
            elif isinstance(value, list | dict):
                normalized[key] = stable_json(value)
            elif hasattr(value, "isoformat"):
                normalized[key] = value.isoformat()
            elif isinstance(value, StepKind | StepStatus | TaskStatus | AttemptStatus):
                normalized[key] = value.value
            else:
                normalized[key] = value
        return normalized

    def _normalize_step_updates(self, updates: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_updates(updates)
        if "artifact_paths" in normalized:
            normalized["artifact_paths_json"] = normalized.pop("artifact_paths")
        return normalized

    def _update_row(
        self,
        conn: sqlite3.Connection,
        table: str,
        pk_name: str,
        pk_value: str,
        updates: dict[str, Any],
    ) -> None:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = list(updates.values()) + [pk_value]
        conn.execute(f"UPDATE {table} SET {assignments} WHERE {pk_name} = ?", values)

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            title=row["title"],
            source_session_key=row["source_session_key"],
            source_channel=row["source_channel"],
            source_chat_id=row["source_chat_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
            status=TaskStatus(row["status"]),
            goal=row["goal"],
            plan=json.loads(row["plan_json"]),
            current_step_index=row["current_step_index"],
            next_run_at=self._parse_dt(row["next_run_at"]),
            lease_owner=row["lease_owner"],
            lease_expires_at=self._parse_dt(row["lease_expires_at"])
            if row["lease_expires_at"]
            else None,
            retry_budget=row["retry_budget"],
            last_error=row["last_error"],
            last_summary=row["last_summary"],
            waiting_prompt=row["waiting_prompt"],
            waiting_alert_hash=row["waiting_alert_hash"],
            waiting_alert_sent_at=self._parse_dt(row["waiting_alert_sent_at"])
            if row["waiting_alert_sent_at"]
            else None,
            notify_channel=row["notify_channel"],
            notify_chat_id=row["notify_chat_id"],
            notify_message_ref=row["notify_message_ref"],
            reply_message_id=row["reply_message_id"],
            final_report_max_chars=row["final_report_max_chars"],
            shared_state=json.loads(row["shared_state_json"]),
        )

    def _row_to_step(self, row: sqlite3.Row) -> Step:
        return Step(
            id=row["id"],
            task_id=row["task_id"],
            step_index=row["step_index"],
            title=row["title"],
            kind=StepKind(row["kind"]),
            instructions=row["instructions"],
            verification=json.loads(row["verification_json"]),
            status=StepStatus(row["status"]),
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            result_summary=row["result_summary"],
            artifact_paths=json.loads(row["artifact_paths_json"] or "[]"),
            started_at=self._parse_dt(row["started_at"]) if row["started_at"] else None,
            finished_at=self._parse_dt(row["finished_at"]) if row["finished_at"] else None,
        )

    def _parse_dt(self, value: str):
        from datetime import datetime

        return datetime.fromisoformat(value)
