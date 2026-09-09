from __future__ import annotations

import time
from dataclasses import dataclass

from .delivery import build_delivery_payload
from .integrations import AgentRunner, NotificationTransport
from .runtime import NotificationSink, Scheduler, Worker
from .storage import TaskStore


class TransportNotificationSink(NotificationSink):
    def __init__(self, transport: NotificationTransport, store: TaskStore | None = None):
        self.transport = transport
        self.store = store
        self._last_error: str | None = None

    def send(self, task, message: str) -> bool:
        artifact_paths = task.shared_state.get("last_completed_step", {}).get("artifact_paths", [])
        rendered = message
        final_message, attachments = build_delivery_payload(task, rendered, artifact_paths)
        ok = self.transport.send(task, final_message, attachments)
        self._last_error = self.transport.last_error()
        return ok

    def last_error(self) -> str | None:
        return self._last_error


@dataclass(slots=True)
class RuntimeService:
    store: TaskStore
    runner: AgentRunner
    notifier: NotificationTransport

    def build_scheduler(self) -> Scheduler:
        sink = TransportNotificationSink(self.notifier, self.store)
        worker = Worker(
            store=self.store,
            executor=_RunnerExecutor(self.runner, self.store.settings.db_path),
            notifier=sink,
        )
        return Scheduler(store=self.store, worker=worker)

    def run_forever(self, worker_id: str = "scheduler", sleep_seconds: int | None = None) -> None:
        scheduler = self.build_scheduler()
        interval = (
            self.store.settings.scheduler_interval_seconds
            if sleep_seconds is None
            else sleep_seconds
        )
        if interval <= 0:
            raise ValueError("Positive scheduler interval required")
        while True:
            scheduler.tick(worker_id)
            time.sleep(interval)


class _RunnerExecutor:
    def __init__(self, runner: AgentRunner, journal_path):
        self.runner = runner
        self.journal_path = journal_path

    def execute(self, task, step, attempt_count, artifacts_dir):
        if step.kind.value == "maker":
            from .maker_executor import MakerStepExecutor

            return MakerStepExecutor(self.journal_path).execute(
                task, step, attempt_count, artifacts_dir
            )
        return self.runner.run_step(task, step, attempt_count, artifacts_dir)
