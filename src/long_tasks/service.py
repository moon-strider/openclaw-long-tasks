from __future__ import annotations

import time
from dataclasses import dataclass

from .delivery import build_delivery_payload
from .integrations import AgentRunner, NotificationTransport
from .responders import OpenClawCliTaskResponder
from .runtime import NotificationSink, Scheduler, Worker
from .storage import TaskStore


class TransportNotificationSink(NotificationSink):
    def __init__(self, transport: NotificationTransport, responder: OpenClawCliTaskResponder | None = None, store: TaskStore | None = None):
        self.transport = transport
        self.responder = responder
        self.store = store
        self._last_error: str | None = None

    def send(self, task, message: str) -> bool:
        step = self.store.get_current_step(task.id) if self.store is not None else None
        artifact_paths = step.artifact_paths if step is not None else []
        rendered = self.responder.render(task, message, final=(task.status.value == 'completed')) if self.responder else message
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
    responder: OpenClawCliTaskResponder | None = None

    def build_scheduler(self) -> Scheduler:
        sink = TransportNotificationSink(self.notifier, self.responder, self.store)
        worker = Worker(store=self.store, executor=_RunnerExecutor(self.runner), notifier=sink)
        return Scheduler(store=self.store, worker=worker)

    def run_forever(self, worker_id: str = 'scheduler', sleep_seconds: int | None = None) -> None:
        scheduler = self.build_scheduler()
        interval = sleep_seconds or self.store.settings.scheduler_interval_seconds
        while True:
            scheduler.tick(worker_id)
            time.sleep(interval)


class _RunnerExecutor:
    def __init__(self, runner: AgentRunner):
        self.runner = runner

    def execute(self, task, step, attempt_count, artifacts_dir):
        return self.runner.run_step(task, step, attempt_count, artifacts_dir)
