from __future__ import annotations

import json
from pathlib import Path

import pytest

from long_tasks.config import Settings
from long_tasks.models import (
    Step,
    StepKind,
    StepStatus,
    Task,
    TaskStatus,
    ensure_step_transition,
    ensure_task_transition,
)
from long_tasks.runtime import (
    DeterministicVerifier,
    InMemoryNotificationSink,
    NeedsUserInput,
    Scheduler,
    Worker,
    compute_retry_backoff,
)
from long_tasks.storage import TaskStore
from long_tasks.utils import new_id, utcnow


class ScriptedExecutor:
    def __init__(self, scripts):
        self.scripts = scripts
        self.calls = []

    def execute(self, task, step, attempt_count, artifacts_dir):
        self.calls.append((task.id, step.step_index, attempt_count))
        fn = self.scripts[step.step_index]
        return fn(task, step, attempt_count, artifacts_dir)


@pytest.fixture()
def store(tmp_path: Path):
    settings = Settings(state_dir=tmp_path / "state")
    return TaskStore(settings=settings)


def make_task_bundle(
    store: TaskStore, *, status: TaskStatus = TaskStatus.READY, step_max_attempts: int = 3
):
    now = utcnow()
    task_id = new_id()
    task = Task(
        id=task_id,
        title="Research meds",
        source_session_key="session",
        source_channel="telegram",
        source_chat_id="chat",
        created_at=now,
        updated_at=now,
        status=status,
        goal="research three topics",
        plan={"steps": ["БРА", "иАПФ", "БМКК"]},
        current_step_index=0,
        next_run_at=now,
        lease_owner=None,
        lease_expires_at=None,
        retry_budget=0,
        last_error=None,
        last_summary=None,
        waiting_prompt=None,
        waiting_alert_hash=None,
        waiting_alert_sent_at=None,
        notify_channel="telegram",
        notify_chat_id="test-chat-id",
        notify_message_ref=None,
        shared_state={},
    )
    steps = [
        Step(
            id=new_id(),
            task_id=task_id,
            step_index=i,
            title=topic,
            kind=StepKind.RESEARCH,
            instructions=f"Research {topic}",
            verification={"json_keys": ["summary"], "artifact_keys": ["result"]},
            max_attempts=step_max_attempts,
        )
        for i, topic in enumerate(["БРА", "иАПФ", "БМКК"])
    ]
    store.create_task(task, steps)
    return task, steps


def write_artifact(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_task_transitions():
    ensure_task_transition(TaskStatus.PLANNING, TaskStatus.READY)
    ensure_task_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        ensure_task_transition(TaskStatus.READY, TaskStatus.COMPLETED)


def test_step_transitions():
    ensure_step_transition(StepStatus.PENDING, StepStatus.IN_PROGRESS)
    ensure_step_transition(StepStatus.IN_PROGRESS, StepStatus.PENDING)
    with pytest.raises(ValueError):
        ensure_step_transition(StepStatus.DONE, StepStatus.PENDING)


def test_lease_acquisition_is_exclusive(store: TaskStore):
    task, _ = make_task_bundle(store)
    first = store.acquire_lease(task.id, "worker-a")
    second = store.acquire_lease(task.id, "worker-b")
    assert first is not None
    assert second is None


def test_expired_lease_reclaimable(store: TaskStore):
    task, _ = make_task_bundle(store)
    first = store.acquire_lease(task.id, "worker-a")
    assert first is not None
    with store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", task.id),
        )
    second = store.acquire_lease(task.id, "worker-b")
    assert second is not None
    assert second.reclaimed_expired_lease is True


def test_retry_backoff():
    assert compute_retry_backoff(1).total_seconds() == 60
    assert compute_retry_backoff(2).total_seconds() == 300
    assert compute_retry_backoff(3).total_seconds() == 900
    assert compute_retry_backoff(4).total_seconds() == 1800
    assert compute_retry_backoff(99).total_seconds() == 1800


def test_verifier(tmp_path: Path):
    artifact = tmp_path / "result.json"
    artifact.write_text('{"ok": true}', encoding="utf-8")
    step = Step(
        id="s",
        task_id="t",
        step_index=0,
        title="verify",
        kind=StepKind.RESEARCH,
        instructions="",
        verification={
            "file_exists": [str(artifact)],
            "file_contains": [{"path": str(artifact), "contains": "ok"}],
            "json_keys": ["summary"],
            "artifact_keys": ["result"],
        },
    )
    result = DeterministicVerifier().verify(
        step, {"summary": "done", "artifacts": {"result": str(artifact)}}
    )
    assert result.ok is True


def test_create_plan_execute_verify_complete(store: TaskStore):
    task, _ = make_task_bundle(store)

    def make_fn(label):
        def fn(task, step, attempt_count, artifacts_dir):
            artifact = artifacts_dir / f"{step.step_index}.json"
            write_artifact(artifact, {"topic": label})
            return {"summary": f"researched {label}", "artifacts": {"result": str(artifact)}}

        return fn

    executor = ScriptedExecutor({0: make_fn("БРА"), 1: make_fn("иАПФ"), 2: make_fn("БМКК")})
    notifier = InMemoryNotificationSink()
    worker = Worker(store, executor, notifier)
    scheduler = Scheduler(store, worker)

    for idx in range(3):
        results = scheduler.tick("worker")
        assert len(results) == 1
        task_state = store.get_task(task.id)
        assert task_state is not None
        if idx < 2:
            assert task_state.status is TaskStatus.READY
            assert task_state.current_step_index == idx + 1
        else:
            assert task_state.status is TaskStatus.COMPLETED

    assert len(notifier.messages) == 3
    assert store.get_task(task.id).status is TaskStatus.COMPLETED


def test_shared_state_accumulates_verified_step_context(store: TaskStore):
    task, _ = make_task_bundle(store)

    def make_fn(label):
        def fn(task, step, attempt_count, artifacts_dir):
            artifact = artifacts_dir / f"{step.step_index}.json"
            write_artifact(artifact, {"topic": label})
            return {
                "summary": f"researched {label}",
                "artifacts": {"result": str(artifact)},
                "shared_state": {"latest_topic": label},
            }

        return fn

    executor = ScriptedExecutor({0: make_fn("БРА"), 1: make_fn("иАПФ"), 2: make_fn("БМКК")})
    worker = Worker(store, executor, InMemoryNotificationSink())
    scheduler = Scheduler(store, worker)

    scheduler.tick("worker")
    after_first = store.get_task(task.id)
    assert after_first is not None
    assert after_first.shared_state["last_completed_step"]["title"] == "БРА"
    assert after_first.shared_state["runner_shared_state"]["latest_topic"] == "БРА"
    assert len(after_first.shared_state["completed_steps"]) == 1

    scheduler.tick("worker")
    after_second = store.get_task(task.id)
    assert after_second is not None
    assert [item["title"] for item in after_second.shared_state["completed_steps"]] == [
        "БРА",
        "иАПФ",
    ]


def test_runner_receives_previous_step_context_in_task_packet(store: TaskStore):
    task, _ = make_task_bundle(store)
    packets: list[dict] = []

    def step0(task, step, attempt_count, artifacts_dir):
        artifact = artifacts_dir / "0.json"
        write_artifact(artifact, {"topic": step.title})
        return {
            "summary": f"done {step.title}",
            "artifacts": {"result": str(artifact)},
            "shared_state": {"step0_done": True},
        }

    def step1(task, step, attempt_count, artifacts_dir):
        packets.append(
            {
                "shared_state": task.shared_state,
                "completed_steps": task.shared_state.get("completed_steps", []),
            }
        )
        artifact = artifacts_dir / "1.json"
        write_artifact(artifact, {"topic": step.title})
        return {"summary": f"done {step.title}", "artifacts": {"result": str(artifact)}}

    executor = ScriptedExecutor({0: step0, 1: step1, 2: step1})
    worker = Worker(store, executor, InMemoryNotificationSink())
    scheduler = Scheduler(store, worker)

    scheduler.tick("worker")
    scheduler.tick("worker")

    assert packets
    assert packets[0]["shared_state"]["runner_shared_state"]["step0_done"] is True
    assert packets[0]["completed_steps"][0]["title"] == "БРА"


def test_waiting_user_resume_execute_complete(store: TaskStore):
    task, _ = make_task_bundle(store)

    def step0(task, step, attempt_count, artifacts_dir):
        raise NeedsUserInput("confirm search scope")

    def step_ok(task, step, attempt_count, artifacts_dir):
        artifact = artifacts_dir / f"{step.step_index}.json"
        write_artifact(artifact, {"topic": step.title})
        return {"summary": f"done {step.title}", "artifacts": {"result": str(artifact)}}

    executor = ScriptedExecutor({0: step0, 1: step_ok, 2: step_ok})
    notifier = InMemoryNotificationSink()
    worker = Worker(store, executor, notifier)
    scheduler = Scheduler(store, worker)

    first = scheduler.tick("worker")
    assert first[0].task_status is TaskStatus.WAITING_USER
    resumed = store.resume_task(task.id)
    assert resumed.status is TaskStatus.READY

    executor.scripts[0] = step_ok
    scheduler.tick("worker")
    scheduler.tick("worker")
    scheduler.tick("worker")
    assert store.get_task(task.id).status is TaskStatus.COMPLETED


def test_worker_crash_lease_expiry_recovery_resume_same_step(store: TaskStore):
    task, _ = make_task_bundle(store)

    class CrashExecutor:
        def __init__(self):
            self.calls = 0

        def execute(self, task, step, attempt_count, artifacts_dir):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            artifact = artifacts_dir / "0.json"
            write_artifact(artifact, {"topic": step.title})
            return {"summary": f"done {step.title}", "artifacts": {"result": str(artifact)}}

    crash = CrashExecutor()
    notifier = InMemoryNotificationSink()
    worker = Worker(store, crash, notifier)
    scheduler = Scheduler(store, worker)

    scheduler.tick("worker")
    with store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, lease_owner = ?, lease_expires_at = ? WHERE id = ?",
            (TaskStatus.RUNNING.value, "dead-worker", "2000-01-01T00:00:00+00:00", task.id),
        )
    recovered = store.recover_expired_running_tasks()
    assert task.id in recovered

    scheduler.tick("worker-2")
    assert store.get_task(task.id).current_step_index == 1


def test_verification_fail_retry_same_step(store: TaskStore):
    task, _ = make_task_bundle(store)

    def bad(task, step, attempt_count, artifacts_dir):
        return {"summary": "missing artifacts"}

    executor = ScriptedExecutor({0: bad, 1: bad, 2: bad})
    notifier = InMemoryNotificationSink()
    worker = Worker(store, executor, notifier)
    scheduler = Scheduler(store, worker)

    scheduler.tick("worker")
    task_state = store.get_task(task.id)
    step = store.get_current_step(task.id)
    assert task_state.status is TaskStatus.READY
    assert step.status is StepStatus.PENDING
    assert task_state.last_error is not None


def test_retries_exhausted_failed(store: TaskStore):
    task, _ = make_task_bundle(store, step_max_attempts=1)

    def bad(task, step, attempt_count, artifacts_dir):
        return {"summary": "missing artifacts"}

    executor = ScriptedExecutor({0: bad, 1: bad, 2: bad})
    notifier = InMemoryNotificationSink()
    worker = Worker(store, executor, notifier)
    scheduler = Scheduler(store, worker)

    scheduler.tick("worker")
    assert store.get_task(task.id).status is TaskStatus.FAILED


def test_notification_state_persisted_before_send_attempt(store: TaskStore):
    task, _ = make_task_bundle(store)
    observed = {}

    class InspectingSink(InMemoryNotificationSink):
        def send(self, task, message):
            observed["status"] = store.get_task(task.id).status
            return super().send(task, message)

    def wait(task, step, attempt_count, artifacts_dir):
        raise NeedsUserInput("need confirmation")

    worker = Worker(store, ScriptedExecutor({0: wait, 1: wait, 2: wait}), InspectingSink())
    Scheduler(store, worker).tick("worker")
    assert observed["status"] is TaskStatus.WAITING_USER


def test_startup_recovery_requeues_expired_running_tasks(store: TaskStore):
    task, _ = make_task_bundle(store)
    with store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, lease_owner = ?, lease_expires_at = ? WHERE id = ?",
            (TaskStatus.RUNNING.value, "worker", "2000-01-01T00:00:00+00:00", task.id),
        )
    recovered = store.recover_expired_running_tasks()
    recovered_task = store.get_task(task.id)
    assert recovered == [task.id]
    assert recovered_task.status is TaskStatus.READY
