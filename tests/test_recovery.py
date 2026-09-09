import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from long_tasks import storage
from long_tasks.api import create_task
from long_tasks.config import Settings
from long_tasks.models import StepStatus, TaskStatus
from long_tasks.resume import resume_latest_waiting_task_for_chat
from long_tasks.runtime import (
    InMemoryNotificationSink,
    NeedsUserInput,
    Scheduler,
    StepDeferred,
    Worker,
)
from long_tasks.storage import TaskStore


def setup(tmp_path, executor, *, attempts=3, notifier=None, ttl=1, refresh=0.05):
    store = TaskStore(
        Settings(state_dir=tmp_path, lease_ttl_seconds=ttl, lease_refresh_seconds=refresh)
    )
    task = create_task(
        store,
        title="Task",
        goal="Verify durable progress",
        steps=[{"title": "step", "instructions": "perform step", "max_attempts": attempts}],
    )
    worker = Worker(store, executor, notifier or InMemoryNotificationSink())
    return store, task, worker


class Function:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, *args):
        return self.fn(*args)


def okay(*args):
    return {"summary": "done"}


def test_heartbeat_prevents_second_worker_during_long_step(tmp_path, monkeypatch):
    entered, release, refreshed = threading.Event(), threading.Event(), threading.Event()

    def blocking(*args):
        entered.set()
        assert release.wait(10)
        return okay()

    store, task, worker = setup(tmp_path, Function(blocking), ttl=10, refresh=0.02)
    clock = [storage.utcnow()]
    refresh_at = clock[0] + timedelta(seconds=9)
    monkeypatch.setattr(storage, "utcnow", lambda: clock[0])
    refresh = store.refresh_lease

    def observe_refresh(task_id, token):
        now = clock[0]
        renewed = refresh(task_id, token, now=now)
        if renewed and now >= refresh_at:
            refreshed.set()
        return renewed

    monkeypatch.setattr(store, "refresh_lease", observe_refresh)
    with ThreadPoolExecutor() as pool:
        first = pool.submit(worker.run_pass, task.id, "same-worker-name")
        try:
            assert entered.wait(5)
            clock[0] = refresh_at
            assert refreshed.wait(5)
            # Advance past the original lease, but within the real heartbeat's renewal.
            clock[0] += timedelta(seconds=2)
            assert not store.recover_expired_running_tasks()
            assert not worker.run_pass(task.id, "same-worker-name").did_work
        finally:
            release.set()
        assert first.result().task_status == TaskStatus.COMPLETED
    assert len(store.list_attempts(task.id)) == 1


def test_cancelled_worker_cannot_commit_or_notify(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def blocking(*args):
        entered.set()
        assert release.wait(5)
        return okay()

    sink = InMemoryNotificationSink()
    store, task, worker = setup(tmp_path, Function(blocking), notifier=sink)
    with ThreadPoolExecutor() as pool:
        work = pool.submit(worker.run_pass, task.id, "worker")
        assert entered.wait(3)
        store.cancel_task(task.id)
        release.set()
        assert not work.result().did_work
    assert store.get_task(task.id).status == TaskStatus.CANCELLED
    assert not sink.messages


def test_expired_owner_cannot_refresh_or_commit(tmp_path):
    def steal(task, *args):
        with store.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (task.id,),
            )
        assert not store.refresh_lease(task.id, store.get_task(task.id).lease_owner)
        return okay()

    store, task, worker = setup(tmp_path, Function(steal), refresh=0.5)
    assert not worker.run_pass(task.id, "worker").did_work
    assert store.get_task(task.id).status == TaskStatus.RUNNING
    store.recover_expired_running_tasks()
    assert store.get_current_step(task.id).status == StepStatus.PENDING
    worker.executor = Function(okay)
    assert worker.run_pass(task.id, "worker").task_status == TaskStatus.COMPLETED


def test_crash_recovers_real_in_progress_step_and_preserves_attempt_budget(tmp_path):
    def crash(*args):
        raise KeyboardInterrupt()

    store, task, worker = setup(tmp_path, Function(crash), attempts=1)
    with pytest.raises(KeyboardInterrupt):
        worker.run_pass(task.id, "worker")
    assert store.get_current_step(task.id).status == StepStatus.IN_PROGRESS
    store.recover_expired_running_tasks()
    assert store.get_current_step(task.id).status == StepStatus.PENDING
    assert store.list_attempts(task.id)[0]["finished_at"]
    worker.executor = Function(okay)
    assert worker.run_pass(task.id, "worker").task_status == TaskStatus.BLOCKED
    store.resume_task(task.id)
    assert worker.run_pass(task.id, "worker").task_status == TaskStatus.COMPLETED


def test_manual_resume_failed_step_actually_runs_with_new_budget(tmp_path):
    store, task, worker = setup(tmp_path, Function(lambda *args: {}), attempts=1)
    assert worker.run_pass(task.id, "worker").task_status == TaskStatus.FAILED
    store.resume_task(task.id)
    assert store.get_current_step(task.id).status == StepStatus.PENDING
    worker.executor = Function(okay)
    assert worker.run_pass(task.id, "worker").task_status == TaskStatus.COMPLETED
    assert len(store.list_attempts(task.id)) == 2


def test_user_reply_reaches_next_execution_packet(tmp_path):
    def ask(*args):
        raise NeedsUserInput("Which colour?")

    store, task, worker = setup(tmp_path, Function(ask))
    worker.run_pass(task.id, "worker")
    assert resume_latest_waiting_task_for_chat(store, "local", "blue") == task.id

    def answered(task, *args):
        assert task.shared_state["user_reply"] == {"prompt": "Which colour?", "text": "blue"}
        return okay()

    worker.executor = Function(answered)
    assert worker.run_pass(task.id, "worker").task_status == TaskStatus.COMPLETED


def test_outbox_failure_does_not_retry_successful_execution(tmp_path):
    class BrokenSink:
        def send(self, task, message):
            raise RuntimeError("transport offline")

    store, task, worker = setup(tmp_path, Function(okay), notifier=BrokenSink())
    assert worker.run_pass(task.id, "worker").task_status == TaskStatus.COMPLETED
    assert not Scheduler(store, worker).tick("worker")
    assert len(store.list_attempts(task.id)) == 1
    with store.transaction() as conn:
        row = conn.execute("SELECT * FROM notifications").fetchone()
        assert row["status"] == "pending" and row["attempt_count"] == 1
        assert json.loads(row["snapshot_json"])["status"] == "completed"
        conn.execute("UPDATE notifications SET next_attempt_at=NULL")
    sink = InMemoryNotificationSink()
    worker.notifier = sink
    worker.deliver_notifications()
    assert len(sink.messages) == 1


def test_outbox_insert_and_checkpoint_roll_back_together(tmp_path, monkeypatch):
    store, task, worker = setup(tmp_path, Function(okay))

    def crash(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "enqueue_notification", crash)
    with pytest.raises(OSError):
        worker.run_pass(task.id, "worker")
    assert store.get_current_step(task.id).status == StepStatus.IN_PROGRESS
    assert store.get_task(task.id).status == TaskStatus.RUNNING
    assert store.list_attempts(task.id)[0]["finished_at"] is None


def test_notification_claim_is_exclusive_and_retries_stop(tmp_path):
    class Broken:
        def send(self, *args):
            return False

    store, task, worker = setup(tmp_path, Function(okay), notifier=Broken())
    worker.run_pass(task.id, "worker")
    for _ in range(7):
        with store.transaction() as conn:
            conn.execute("UPDATE notifications SET next_attempt_at=NULL")
        worker.deliver_notifications()
    with store.transaction() as conn:
        row = conn.execute("SELECT * FROM notifications").fetchone()
        assert row["status"] == "dead" and row["attempt_count"] == 8
    assert store.claim_notification(row["id"], "new") is None


def test_deferred_checkpoint_does_not_consume_retry_budget(tmp_path):
    calls = []

    def sliced(*args):
        calls.append(args[-1])
        if len(calls) <= 4:
            raise StepDeferred("checkpoint saved")
        return okay()

    store, task, worker = setup(tmp_path, Function(sliced), attempts=1)
    for _ in range(4):
        assert worker.run_pass(task.id, "worker").task_status == TaskStatus.READY
        assert store.get_current_step(task.id).attempt_count == 0
    assert worker.run_pass(task.id, "worker").task_status == TaskStatus.COMPLETED
    assert len(set(calls)) == 5


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [{"title": "x", "instructions": "x", "surprise": True}],
        [{"title": "x", "instructions": "x", "verification": {"typo": True}}],
        [{"title": "x", "instructions": "x", "max_attempts": True}],
    ],
)
def test_invalid_plans_never_enter_database(tmp_path, steps):
    store = TaskStore(Settings(state_dir=tmp_path))
    with pytest.raises(ValueError):
        create_task(store, title="test", goal="test", steps=steps)
    assert not store.list_tasks()
