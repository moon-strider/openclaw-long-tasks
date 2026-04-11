from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .models import AttemptStatus, Step, StepStatus, Task, TaskStatus
from .storage import TaskStore
from .utils import new_id, utcnow, waiting_alert_hash


class StepExecutor(Protocol):
    def execute(self, task: Task, step: Step, attempt_count: int, artifacts_dir: Path) -> dict[str, Any]: ...


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
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, task: Task, message: str) -> bool:
        self.messages.append((task.id, message))
        return True


class DeterministicVerifier:
    def verify(self, step: Step, output: dict[str, Any]) -> VerificationResult:
        details: list[str] = []
        rules = step.verification

        expected_exit_code = rules.get("command_exit_code")
        if expected_exit_code is not None:
            actual = output.get("command_exit_code")
            if actual != expected_exit_code:
                return VerificationResult(False, [f"command_exit_code expected {expected_exit_code}, got {actual}"])
            details.append(f"command_exit_code={actual}")

        for path_str in rules.get("file_exists", []):
            path = Path(path_str)
            if not path.exists():
                return VerificationResult(False, [f"missing file: {path}"])
            details.append(f"file_exists={path}")

        for item in rules.get("file_contains", []):
            path = Path(item["path"])
            needle = item["contains"]
            if not path.exists():
                return VerificationResult(False, [f"missing file for content check: {path}"])
            text = path.read_text(encoding="utf-8")
            if needle not in text:
                return VerificationResult(False, [f"file missing expected content: {path} -> {needle}"])
            details.append(f"file_contains={path}")

        for key in rules.get("json_keys", []):
            if key not in output:
                return VerificationResult(False, [f"missing json key: {key}"])
            details.append(f"json_key={key}")

        artifact_keys = rules.get("artifact_keys", [])
        for key in artifact_keys:
            if key not in output.get("artifacts", {}):
                return VerificationResult(False, [f"missing artifact key: {key}"])
            details.append(f"artifact_key={key}")

        return VerificationResult(True, details)


def compute_retry_backoff(attempt_number: int) -> timedelta:
    if attempt_number <= 1:
        return timedelta(minutes=1)
    if attempt_number == 2:
        return timedelta(minutes=5)
    if attempt_number == 3:
        return timedelta(minutes=15)
    return timedelta(minutes=30)


class Worker:
    def __init__(
        self,
        store: TaskStore,
        executor: StepExecutor,
        notifier: NotificationSink,
        verifier: DeterministicVerifier | None = None,
        settings: Settings | None = None,
    ):
        self.store = store
        self.executor = executor
        self.notifier = notifier
        self.verifier = verifier or DeterministicVerifier()
        self.settings = settings or store.settings

    def run_pass(self, task_id: str, worker_id: str) -> WorkerPassResult:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        step = self.store.get_current_step(task_id)
        if step is None:
            raise ValueError(f"Task {task_id} has no current step")

        attempt_id = new_id()
        now = utcnow()
        artifacts_dir = self.settings.artifacts_dir / task.id
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / 'shared').mkdir(parents=True, exist_ok=True)

        with self.store.transaction() as conn:
            current_row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task.id,)).fetchone()
            current_status = TaskStatus(current_row["status"])
            if current_status is TaskStatus.READY:
                self.store.transition_task_status(conn, task.id, TaskStatus.RUNNING, last_error=None)
            elif current_status is TaskStatus.RUNNING:
                conn.execute("UPDATE tasks SET last_error = NULL, updated_at = ? WHERE id = ?", (now.isoformat(), task.id))
            else:
                raise ValueError(f"Task {task.id} is not runnable from {current_status}")
            self.store.transition_step_status(conn, step.id, StepStatus.IN_PROGRESS, started_at=now)
            conn.execute(
                "UPDATE steps SET attempt_count = attempt_count + 1 WHERE id = ?",
                (step.id,),
            )
            self.store.record_attempt_start(
                conn,
                attempt_id=attempt_id,
                task_id=task.id,
                step_id=step.id,
                role="worker",
                worker_id=worker_id,
                snapshot={"task_goal": task.goal, "step": step.title, "step_index": step.step_index},
            )
            self.store.add_event(conn, new_id(), task.id, "worker.pass.started", {"step_index": step.step_index}, step.id)

        step = self.store.get_current_step(task_id)
        assert step is not None

        try:
            output = self.executor.execute(task, step, step.attempt_count, artifacts_dir)
            verification = self.verifier.verify(step, output)
            if not verification.ok:
                return self._retry_or_fail(task_id, step.id, attempt_id, step.attempt_count, verification.details[0], output)
            return self._complete_step(task_id, step.id, attempt_id, output, verification.details)
        except NeedsUserInput as exc:
            return self._mark_waiting_user(task_id, step.id, attempt_id, str(exc))
        except TaskBlocked as exc:
            return self._mark_blocked(task_id, step.id, attempt_id, str(exc))
        except Exception as exc:
            return self._retry_or_fail(task_id, step.id, attempt_id, step.attempt_count, str(exc), None)

    def _complete_step(self, task_id: str, step_id: str, attempt_id: str, output: dict[str, Any], verification_details: list[str]) -> WorkerPassResult:
        task = self.store.get_task(task_id)
        step = self.store.get_current_step(task_id)
        assert task is not None and step is not None
        now = utcnow()
        summary = output.get("summary", f"Completed step {step.step_index}: {step.title}")
        artifacts = output.get("artifacts", {})
        artifact_paths = [str(v) for v in artifacts.values()]
        shared_state = self._build_next_shared_state(task, step, summary, artifact_paths, output)
        all_steps = self.store.get_steps(task_id)
        is_final_step = step.step_index >= len(all_steps) - 1

        with self.store.transaction() as conn:
            self.store.transition_step_status(
                conn,
                step_id,
                StepStatus.DONE,
                finished_at=now,
                result_summary=summary,
                artifact_paths=artifact_paths,
            )
            if is_final_step:
                self.store.transition_task_status(
                    conn,
                    task_id,
                    TaskStatus.COMPLETED,
                    last_summary=summary,
                    next_run_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
                self.store.set_task_shared_state(conn, task_id, shared_state)
            else:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, current_step_index = ?, last_summary = ?, next_run_at = ?,
                        lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        TaskStatus.READY.value,
                        step.step_index + 1,
                        summary,
                        now.isoformat(),
                        now.isoformat(),
                        task_id,
                    ),
                )
                self.store.set_task_shared_state(conn, task_id, shared_state)
            self.store.record_attempt_finish(conn, attempt_id, AttemptStatus.SUCCESS, output={"verification": verification_details, **output})
            self.store.add_event(conn, new_id(), task_id, "step.completed", {"step_index": step.step_index, "summary": summary}, step_id)
            if is_final_step:
                self.store.add_event(conn, new_id(), task_id, "task.completed", {"summary": summary}, step_id)

        refreshed = self.store.get_task(task_id)
        assert refreshed is not None
        current_step = refreshed.current_step_index
        next_action = "done" if refreshed.status is TaskStatus.COMPLETED else f"step {current_step}"
        message = self._format_summary(refreshed, step.title, summary, next_action, False, refreshed.next_run_at.isoformat())
        self._queue_and_send_notification(refreshed, message)
        return WorkerPassResult(refreshed.id, refreshed.status, refreshed.current_step_index, message, True, False, refreshed.next_run_at.isoformat())

    def _build_next_shared_state(self, task: Task, step: Step, summary: str, artifact_paths: list[str], output: dict[str, Any]) -> dict[str, Any]:
        completed_steps = []
        for item in task.shared_state.get('completed_steps', []):
            if isinstance(item, dict):
                completed_steps.append(item)

        completed_steps.append({
            'step_index': step.step_index,
            'title': step.title,
            'summary': summary,
            'artifact_paths': artifact_paths,
        })

        next_state = dict(task.shared_state)
        next_state['completed_steps'] = completed_steps
        next_state['last_completed_step'] = {
            'step_index': step.step_index,
            'title': step.title,
            'summary': summary,
            'artifact_paths': artifact_paths,
        }
        runner_shared_state = output.get('shared_state')
        if isinstance(runner_shared_state, dict):
            next_state['runner_shared_state'] = runner_shared_state
        return next_state

    def _retry_or_fail(self, task_id: str, step_id: str, attempt_id: str, attempt_count: int, error: str, output: dict[str, Any] | None) -> WorkerPassResult:
        task = self.store.get_task(task_id)
        step = self.store.get_current_step(task_id)
        assert task is not None and step is not None
        now = utcnow()
        exhausted = attempt_count >= step.max_attempts
        if exhausted:
            target_status = TaskStatus.FAILED if step.status is not StepStatus.BLOCKED else TaskStatus.BLOCKED
            with self.store.transaction() as conn:
                self.store.transition_step_status(conn, step_id, StepStatus.FAILED, finished_at=now, result_summary=error)
                self.store.transition_task_status(
                    conn,
                    task_id,
                    target_status,
                    last_error=error,
                    lease_owner=None,
                    lease_expires_at=None,
                    next_run_at=now,
                )
                self.store.record_attempt_finish(conn, attempt_id, AttemptStatus.FAILED, output=output, error_text=error)
                self.store.add_event(conn, new_id(), task_id, "task.failed", {"error": error}, step_id)
            refreshed = self.store.get_task(task_id)
            assert refreshed is not None
            message = self._format_summary(refreshed, step.title, f"Step failed: {error}", "awaiting explicit resume", True, None)
            self._queue_and_send_notification(refreshed, message)
            return WorkerPassResult(refreshed.id, refreshed.status, refreshed.current_step_index, message, True, True, None)

        backoff = compute_retry_backoff(attempt_count)
        next_run_at = now + backoff
        with self.store.transaction() as conn:
            self.store.transition_step_status(conn, step_id, StepStatus.PENDING, result_summary=error)
            conn.execute(
                "UPDATE tasks SET status = ?, last_error = ?, next_run_at = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                (TaskStatus.READY.value, error, next_run_at.isoformat(), now.isoformat(), task_id),
            )
            self.store.record_attempt_finish(conn, attempt_id, AttemptStatus.RETRY, output=output, error_text=error)
            self.store.add_event(conn, new_id(), task_id, "step.retry_scheduled", {"error": error, "next_run_at": next_run_at.isoformat()}, step_id)
        refreshed = self.store.get_task(task_id)
        assert refreshed is not None
        message = self._format_summary(refreshed, step.title, f"Retry scheduled: {error}", "automatic retry", False, next_run_at.isoformat())
        self._queue_and_send_notification(refreshed, message)
        return WorkerPassResult(refreshed.id, refreshed.status, refreshed.current_step_index, message, True, False, next_run_at.isoformat())

    def _mark_waiting_user(self, task_id: str, step_id: str, attempt_id: str, prompt: str) -> WorkerPassResult:
        task = self.store.get_task(task_id)
        step = self.store.get_current_step(task_id)
        assert task is not None and step is not None
        now = utcnow()
        alert_hash = waiting_alert_hash(prompt, task_id, step.step_index)
        with self.store.transaction() as conn:
            self.store.transition_step_status(conn, step_id, StepStatus.PENDING, result_summary=prompt)
            conn.execute(
                "UPDATE tasks SET status = ?, waiting_prompt = ?, waiting_alert_hash = ?, lease_owner = NULL, lease_expires_at = NULL, next_run_at = ?, updated_at = ? WHERE id = ?",
                (TaskStatus.WAITING_USER.value, prompt, alert_hash, now.isoformat(), now.isoformat(), task_id),
            )
            self.store.record_attempt_finish(conn, attempt_id, AttemptStatus.NEEDS_USER, error_text=prompt)
            self.store.add_event(conn, new_id(), task_id, "task.waiting_user", {"prompt": prompt}, step_id)
        refreshed = self.store.get_task(task_id)
        assert refreshed is not None
        message = self._format_summary(refreshed, step.title, f"Need user input: {prompt}", "waiting for user", True, None)
        sent = self._queue_and_send_notification(refreshed, message)
        if sent:
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE tasks SET waiting_alert_sent_at = ?, updated_at = ? WHERE id = ?",
                    (now.isoformat(), now.isoformat(), task_id),
                )
        return WorkerPassResult(refreshed.id, refreshed.status, refreshed.current_step_index, message, True, True, None)

    def _mark_blocked(self, task_id: str, step_id: str, attempt_id: str, reason: str) -> WorkerPassResult:
        task = self.store.get_task(task_id)
        step = self.store.get_current_step(task_id)
        assert task is not None and step is not None
        now = utcnow()
        with self.store.transaction() as conn:
            self.store.transition_step_status(conn, step_id, StepStatus.BLOCKED, finished_at=now, result_summary=reason)
            conn.execute(
                "UPDATE tasks SET status = ?, last_error = ?, lease_owner = NULL, lease_expires_at = NULL, next_run_at = ?, updated_at = ? WHERE id = ?",
                (TaskStatus.BLOCKED.value, reason, now.isoformat(), now.isoformat(), task_id),
            )
            self.store.record_attempt_finish(conn, attempt_id, AttemptStatus.BLOCKED, error_text=reason)
            self.store.add_event(conn, new_id(), task_id, "task.blocked", {"reason": reason}, step_id)
        refreshed = self.store.get_task(task_id)
        assert refreshed is not None
        message = self._format_summary(refreshed, step.title, f"Blocked: {reason}", "awaiting explicit resume", True, None)
        self._queue_and_send_notification(refreshed, message)
        return WorkerPassResult(refreshed.id, refreshed.status, refreshed.current_step_index, message, True, True, None)

    def _queue_and_send_notification(self, task: Task, message: str) -> bool:
        notification_id = new_id()
        with self.store.transaction() as conn:
            self.store.enqueue_notification(conn, notification_id, task.id, task.notify_channel, task.notify_chat_id, message)
        ok = self.notifier.send(task, message)
        if ok:
            self.store.mark_notification_sent(notification_id)
        else:
            error_text = getattr(self.notifier, 'last_error', lambda: None)() or 'send returned false'
            self.store.mark_notification_failed(notification_id, error_text)
        return ok

    def _format_summary(self, task: Task, step_title: str, did: str, next_action: str, needs_user: bool, next_run_at: str | None) -> str:
        return (
            f"did: {did}\n"
            f"task_state: {task.status.value}\n"
            f"current_step: {task.current_step_index} ({step_title})\n"
            f"next_action: {next_action}\n"
            f"user_input_required: {'yes' if needs_user else 'no'}\n"
            f"next_run_at: {next_run_at or '-'}"
        )


class Scheduler:
    def __init__(self, store: TaskStore, worker: Worker):
        self.store = store
        self.worker = worker

    def tick(self, worker_id: str) -> list[WorkerPassResult]:
        recovered = self.store.recover_expired_running_tasks()
        results: list[WorkerPassResult] = []
        for row in self.store.list_pending_notifications():
            task = self.store.get_task(row['task_id'])
            if task is None:
                continue
            ok = self.worker.notifier.send(task, row['message'])
            if ok:
                self.store.mark_notification_sent(row['id'])
            else:
                error_text = getattr(self.worker.notifier, 'last_error', lambda: None)() or 'retry send returned false'
                self.store.mark_notification_failed(row['id'], error_text)
        for task in self.store.runnable_tasks():
            lease = self.store.acquire_lease(task.id, worker_id)
            if lease is None:
                continue
            if lease.reclaimed_expired_lease:
                with self.store.transaction() as conn:
                    self.store.add_event(conn, new_id(), task.id, "lease.expired", {"reclaimed": True})
            results.append(self.worker.run_pass(task.id, worker_id))
        return results


class NeedsUserInput(RuntimeError):
    pass


class TaskBlocked(RuntimeError):
    pass
