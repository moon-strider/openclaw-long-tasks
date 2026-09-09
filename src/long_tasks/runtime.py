"""Leased macro steps with fenced checkpoints and a transactional outbox."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

from .execution import execution_cancelled
from .models import AttemptStatus, Step, StepStatus, Task, TaskStatus
from .storage import TaskStore
from .utils import new_id, stable_json, utcnow, waiting_alert_hash
from .validation import Rules


class StepExecutor(Protocol):
    def execute(
        self, task: Task, step: Step, attempt_count: int, artifacts_dir: Path
    ) -> dict[str, Any]: ...


class NotificationSink(Protocol):
    def send(self, task: Task, message: str) -> bool: ...


@dataclass(slots=True)
class VerificationResult:
    ok: bool
    details: list[str]


@dataclass(slots=True)
class WorkerPassResult:
    task_id: str
    task_status: TaskStatus
    current_step: int
    message: str
    did_work: bool
    needs_user_input: bool = False
    next_run_at: str | None = None


class InMemoryNotificationSink:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def send(self, task, message):
        self.messages.append((task.id, message))
        return True


class DeterministicVerifier:
    def verify(self, step, output):
        try:
            rules = Rules.model_validate(step.verification)
            if (
                not isinstance(output, dict)
                or not isinstance(output.get("summary"), str)
                or not output["summary"].strip()
            ):
                raise ValueError("A nonempty summary is required")
            if len(stable_json(output).encode()) > 262144:
                raise ValueError("Step output exceeds 256 KiB")
            if rules.command_exit_code is not None and (
                type(output.get("command_exit_code")) is not int
                or output["command_exit_code"] != rules.command_exit_code
            ):
                raise ValueError("Reported command_exit_code differs from expected value")
            for name in rules.file_exists:
                if not Path(name).is_file():
                    raise ValueError(f"Missing file: {name}")
            for item in rules.file_contains:
                with Path(item.path).open("rb") as handle:
                    content = handle.read(1048577)
                if len(content) > 1048576:
                    raise ValueError("Content verification file exceeds one MiB")
                if item.contains not in content.decode("utf-8"):
                    raise ValueError(f"Expected content absent: {item.path}")
            if any(key not in output for key in rules.json_keys):
                raise ValueError("Missing required output key")
            artifacts = output.get("artifacts", {})
            if not isinstance(artifacts, dict) or len(artifacts) > 64:
                raise ValueError("Artifacts must be an object of at most 64 paths")
            if any(key not in artifacts for key in rules.artifact_keys):
                raise ValueError("Missing required artifact key")
            for path in artifacts.values():
                if (
                    not isinstance(path, str)
                    or not Path(path).is_absolute()
                    or not Path(path).is_file()
                ):
                    raise ValueError("Every artifact must name an existing absolute file path")
            if "shared_state" in output and not isinstance(output["shared_state"], dict):
                raise ValueError("shared_state must be an object")
            return VerificationResult(True, ["configured verification rules passed"])
        except (ValueError, TypeError, OSError) as exc:
            return VerificationResult(False, [str(exc)[:1024]])


def compute_retry_backoff(attempt_number):
    return timedelta(minutes=[1, 5, 15, 30][min(max(attempt_number - 1, 0), 3)])


class NeedsUserInput(RuntimeError):
    pass


class TaskBlocked(RuntimeError):
    pass


class StepDeferred(RuntimeError):
    """A microtask checkpoint yielded its lease without consuming a retry."""


class Worker:
    def __init__(self, store, executor, notifier, verifier=None, settings=None):
        self.store, self.executor, self.notifier = store, executor, notifier
        self.verifier = verifier or DeterministicVerifier()
        self.settings = settings or store.settings

    @contextmanager
    def _heartbeat(self, task_id, token, refresh_operation=None):
        stop, cancelled = threading.Event(), threading.Event()
        context_token = execution_cancelled.set(cancelled)

        def refresh():
            while not stop.wait(self.settings.lease_refresh_seconds):
                try:
                    renewed = (
                        refresh_operation()
                        if refresh_operation
                        else self.store.refresh_lease(task_id, token)
                    )
                    if renewed:
                        continue
                except Exception:
                    pass
                cancelled.set()
                return

        thread = threading.Thread(target=refresh, name="long-task-lease", daemon=True)
        thread.start()
        try:
            yield cancelled
        finally:
            stop.set()
            thread.join()
            execution_cancelled.reset(context_token)

    def _idle(self, task_id, message):
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return WorkerPassResult(task.id, task.status, task.current_step_index, message, False)

    def run_pass(self, task_id, worker_id):
        token = worker_id + ":" + new_id()
        if self.store.acquire_lease(task_id, token) is None:
            return self._idle(task_id, "Task is not due or is leased by another worker")
        attempt_id = new_id()
        try:
            with self.store.transaction() as conn:
                if not self.store.owns_lease(conn, task_id, token):
                    return self._idle(task_id, "Lease lost before execution")
                task = self.store._row_to_task(
                    conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                )
                step_row = conn.execute(
                    "SELECT * FROM steps WHERE task_id=? AND step_index=?",
                    (task_id, task.current_step_index),
                ).fetchone()
                if step_row is None:
                    raise ValueError("Task has no current step")
                step = self.store._row_to_step(step_row)
                if step.status == StepStatus.IN_PROGRESS:
                    conn.execute("UPDATE steps SET status='pending' WHERE id=?", (step.id,))
                    conn.execute(
                        "UPDATE attempts SET finished_at=?,error_text='lease reclaimed',status='retry' WHERE task_id=? AND finished_at IS NULL",
                        (utcnow().isoformat(), task_id),
                    )
                conn.execute(
                    "UPDATE tasks SET status='running',last_error=NULL,updated_at=? WHERE id=?",
                    (utcnow().isoformat(), task_id),
                )
                self.store.transition_step_status(
                    conn,
                    step.id,
                    StepStatus.IN_PROGRESS,
                    started_at=utcnow(),
                    attempt_count=step.attempt_count + 1,
                )
                self.store.record_attempt_start(
                    conn,
                    attempt_id,
                    task_id,
                    step.id,
                    "worker",
                    token,
                    {"step_index": step.step_index},
                )
                self.store.add_event(
                    conn,
                    new_id(),
                    task_id,
                    "worker.pass.started",
                    {"attempt_id": attempt_id},
                    step.id,
                )
            artifacts_dir = self.settings.artifacts_dir / task_id / attempt_id
            artifacts_dir.mkdir(parents=True, exist_ok=False)
            step.attempt_count += 1
            output, error, outcome = None, None, "success"
            with self._heartbeat(task_id, token):
                try:
                    if step.attempt_count > step.max_attempts:
                        raise TaskBlocked(
                            "Interrupted attempts exhausted the step budget; explicit resume required"
                        )
                    output = self.executor.execute(task, step, step.attempt_count, artifacts_dir)
                    verification = self.verifier.verify(step, output)
                    if not verification.ok:
                        error, outcome = verification.details[0], "error"
                except StepDeferred as exc:
                    error, outcome = str(exc)[:1024], "deferred"
                except NeedsUserInput as exc:
                    error, outcome = str(exc)[:16384], "waiting"
                except TaskBlocked as exc:
                    error, outcome = str(exc)[:1024], "blocked"
                except Exception as exc:
                    error, outcome = str(exc)[:1024], "error"
                result = self._settle(task, step, attempt_id, token, outcome, output, error)
            self.deliver_notifications()
            return result
        finally:
            self.store.release_lease(task_id, token)

    def _settle(self, task, step, attempt_id, token, outcome, output, error):
        now = utcnow()
        with self.store.transaction() as conn:
            if not self.store.owns_lease(conn, task.id, token):
                return self._idle(task.id, "Discarded result after execution lease was lost")
            index, next_run = step.step_index, now
            shared = dict(task.shared_state)
            paths = []
            if outcome == "success":
                summary = output["summary"][:16384]
                paths = list(output.get("artifacts", {}).values())
                final = (
                    conn.execute(
                        "SELECT count(*) FROM steps WHERE task_id=?", (task.id,)
                    ).fetchone()[0]
                    == index + 1
                )
                status, step_status, attempt_status = (
                    (TaskStatus.COMPLETED if final else TaskStatus.READY),
                    StepStatus.DONE,
                    AttemptStatus.SUCCESS,
                )
                entry = {
                    "step_index": index,
                    "title": step.title,
                    "summary": summary,
                    "artifact_paths": paths,
                }
                shared["completed_steps"] = [*shared.get("completed_steps", []), entry][-32:]
                shared["last_completed_step"] = entry
                if "shared_state" in output:
                    shared["runner_shared_state"] = output["shared_state"]
                index += 0 if final else 1
            else:
                summary = error or "Step failed"
                if outcome == "deferred":
                    status, step_status, attempt_status = (
                        TaskStatus.READY,
                        StepStatus.PENDING,
                        AttemptStatus.YIELDED,
                    )
                    conn.execute(
                        "UPDATE steps SET attempt_count=attempt_count-1 WHERE id=?", (step.id,)
                    )
                elif outcome == "waiting":
                    status, step_status, attempt_status = (
                        TaskStatus.WAITING_USER,
                        StepStatus.PENDING,
                        AttemptStatus.NEEDS_USER,
                    )
                elif outcome == "blocked":
                    status, step_status, attempt_status = (
                        TaskStatus.BLOCKED,
                        StepStatus.BLOCKED,
                        AttemptStatus.BLOCKED,
                    )
                elif step.attempt_count >= step.max_attempts:
                    status, step_status, attempt_status = (
                        TaskStatus.FAILED,
                        StepStatus.FAILED,
                        AttemptStatus.FAILED,
                    )
                else:
                    status, step_status, attempt_status = (
                        TaskStatus.READY,
                        StepStatus.PENDING,
                        AttemptStatus.RETRY,
                    )
                    next_run += compute_retry_backoff(step.attempt_count)
            self.store.transition_step_status(
                conn,
                step.id,
                step_status,
                result_summary=summary,
                artifact_paths=paths,
                finished_at=now if step_status != StepStatus.PENDING else None,
            )
            conn.execute(
                "UPDATE tasks SET status=?,current_step_index=?,last_summary=?,last_error=?,next_run_at=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?,shared_state_json=?,waiting_prompt=?,waiting_alert_hash=? WHERE id=?",
                (
                    status.value,
                    index,
                    summary,
                    error,
                    next_run.isoformat(),
                    now.isoformat(),
                    stable_json(shared),
                    summary if outcome == "waiting" else None,
                    waiting_alert_hash(summary, task.id, index) if outcome == "waiting" else None,
                    task.id,
                ),
            )
            self.store.record_attempt_finish(
                conn, attempt_id, attempt_status, output if outcome == "success" else None, error
            )
            event_id = new_id()
            self.store.add_event(
                conn,
                event_id,
                task.id,
                "step." + attempt_status.value,
                {"summary": summary},
                step.id,
            )
            needs_user = status in {TaskStatus.WAITING_USER, TaskStatus.BLOCKED, TaskStatus.FAILED}
            message = f"did: {summary}\ntask_state: {status.value}\ncurrent_step: {index}\nuser_input_required: {'yes' if needs_user else 'no'}"
            notification_id = new_id()
            self.store.enqueue_notification(
                conn,
                notification_id,
                task.id,
                task.notify_channel,
                task.notify_chat_id,
                message,
                event_id,
            )
            snapshot = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task.id,)).fetchone())
            conn.execute(
                "UPDATE notifications SET snapshot_json=? WHERE id=?",
                (stable_json(snapshot), notification_id),
            )
        return WorkerPassResult(
            task.id,
            status,
            index,
            message,
            True,
            needs_user,
            next_run.isoformat() if status == TaskStatus.READY else None,
        )

    def deliver_notifications(self):
        for pending in self.store.list_pending_notifications():
            token = new_id()
            row = self.store.claim_notification(pending["id"], token)
            if row is None:
                continue
            try:
                task = (
                    self.store._row_to_task(json.loads(row["snapshot_json"]))
                    if row["snapshot_json"]
                    else self.store.get_task(row["task_id"])
                )
                with self._heartbeat(
                    row["task_id"],
                    token,
                    lambda notification_id=row["id"], claim=token: self.store.refresh_notification(
                        notification_id, claim
                    ),
                ):
                    if task is None or not self.notifier.send(task, row["message"]):
                        raise RuntimeError("Notification transport did not acknowledge delivery")
            except Exception as exc:
                self.store.mark_notification_failed(row["id"], type(exc).__name__, token)
            else:
                self.store.mark_notification_sent(row["id"], token)


class Scheduler:
    def __init__(self, store: TaskStore, worker: Worker):
        self.store, self.worker = store, worker

    def tick(self, worker_id):
        self.store.recover_expired_running_tasks()
        self.worker.deliver_notifications()
        results = [self.worker.run_pass(task.id, worker_id) for task in self.store.runnable_tasks()]
        return [result for result in results if result.did_work]
