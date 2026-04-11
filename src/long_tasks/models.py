from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


@dataclass(slots=True)
class Attachment:
    path: str
    kind: str = 'file'
    caption: str | None = None


class TaskStatus(StrEnum):
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def can_transition_to(self, target: "TaskStatus") -> bool:
        allowed = {
            TaskStatus.PLANNING: {TaskStatus.READY, TaskStatus.WAITING_USER, TaskStatus.CANCELLED},
            TaskStatus.READY: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
            TaskStatus.RUNNING: {
                TaskStatus.READY,
                TaskStatus.WAITING_USER,
                TaskStatus.BLOCKED,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.WAITING_USER: {TaskStatus.READY, TaskStatus.CANCELLED},
            TaskStatus.BLOCKED: {TaskStatus.READY, TaskStatus.CANCELLED},
            TaskStatus.FAILED: {TaskStatus.READY, TaskStatus.CANCELLED},
            TaskStatus.COMPLETED: set(),
            TaskStatus.CANCELLED: set(),
        }
        return target in allowed[self]


class StepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"

    def can_transition_to(self, target: "StepStatus") -> bool:
        allowed = {
            StepStatus.PENDING: {StepStatus.IN_PROGRESS},
            StepStatus.IN_PROGRESS: {StepStatus.PENDING, StepStatus.DONE, StepStatus.FAILED, StepStatus.BLOCKED},
            StepStatus.DONE: set(),
            StepStatus.FAILED: set(),
            StepStatus.BLOCKED: set(),
        }
        return target in allowed[self]


class StepKind(StrEnum):
    RESEARCH = "research"
    EXECUTE = "execute"
    VERIFY = "verify"
    NOTIFY = "notify"


class AttemptStatus(StrEnum):
    SUCCESS = "success"
    RETRY = "retry"
    BLOCKED = "blocked"
    NEEDS_USER = "needs_user"
    FAILED = "failed"


@dataclass(slots=True)
class Step:
    id: str
    task_id: str
    step_index: int
    title: str
    kind: StepKind
    instructions: str
    verification: dict[str, Any]
    status: StepStatus = StepStatus.PENDING
    attempt_count: int = 0
    max_attempts: int = 3
    result_summary: str | None = None
    artifact_paths: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True)
class Task:
    id: str
    title: str
    source_session_key: str
    source_channel: str
    source_chat_id: str | None
    created_at: datetime
    updated_at: datetime
    status: TaskStatus
    goal: str
    plan: dict[str, Any]
    current_step_index: int
    next_run_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    retry_budget: int
    last_error: str | None
    last_summary: str | None
    waiting_prompt: str | None
    waiting_alert_hash: str | None
    waiting_alert_sent_at: datetime | None
    notify_channel: str
    notify_chat_id: str
    notify_message_ref: str | None
    reply_message_id: str | None = None
    final_report_max_chars: int = 4000
    shared_state: dict[str, Any] = field(default_factory=dict)


def ensure_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if not current.can_transition_to(target):
        raise ValueError(f"Invalid task transition: {current} -> {target}")


def ensure_step_transition(current: StepStatus, target: StepStatus) -> None:
    if not current.can_transition_to(target):
        raise ValueError(f"Invalid step transition: {current} -> {target}")
