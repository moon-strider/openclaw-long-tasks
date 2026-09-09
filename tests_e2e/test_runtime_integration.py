from __future__ import annotations

from pathlib import Path

from long_tasks.config import Settings
from long_tasks.models import Step, StepKind, Task, TaskStatus
from long_tasks.runtime import InMemoryNotificationSink, Scheduler, Worker
from long_tasks.storage import TaskStore
from long_tasks.utils import new_id, utcnow


class _Executor:
    def execute(self, task, step, attempt_count, artifacts_dir):
        output_path = artifacts_dir / "e2e-result.txt"
        output_path.write_text("E2E long task complete\n", encoding="utf-8")
        return {
            "summary": "E2E runtime task complete",
            "artifacts": {"result": str(output_path)},
        }


def test_runtime_e2e_creates_task_rows_and_artifact(tmp_path: Path):
    store = TaskStore(Settings(state_dir=tmp_path))
    now = utcnow()
    task_id = new_id()
    task = Task(
        id=task_id,
        title="e2e runtime task",
        source_session_key="test-session",
        source_channel="test",
        source_chat_id="chat",
        created_at=now,
        updated_at=now,
        status=TaskStatus.READY,
        goal="write durable artifact",
        plan={"steps": ["write file"]},
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
        notify_channel="test",
        notify_chat_id="chat",
        notify_message_ref=None,
        reply_message_id=None,
    )
    step = Step(
        id=new_id(),
        task_id=task_id,
        step_index=0,
        title="write file",
        kind=StepKind.EXECUTE,
        instructions="write output artifact",
        verification={"artifact_keys": ["result"]},
        max_attempts=1,
    )
    store.create_task(task, [step])

    notifier = InMemoryNotificationSink()
    worker = Worker(store=store, executor=_Executor(), notifier=notifier)
    scheduler = Scheduler(store=store, worker=worker)

    results = scheduler.tick("worker-e2e")

    assert len(results) == 1
    refreshed = store.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status is TaskStatus.COMPLETED
    artifact = Path(store.get_current_step(task.id).artifact_paths[0])
    assert artifact.exists()
    assert "E2E runtime task complete" in notifier.messages[0][1]
    events = list(store.list_events(task.id))
    assert any(event["type"] == "step.success" for event in events)
