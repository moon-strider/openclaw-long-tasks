from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from .api import create_task
from .integrations import OpenClawCliAgentRunner, OpenClawCliNotificationTransport
from .maker_cli import app as maker_app
from .resume import resume_latest_waiting_task_for_chat
from .service import RuntimeService
from .storage import TaskStore

app = typer.Typer(no_args_is_help=True)
tasks_app = typer.Typer(no_args_is_help=True)
service_app = typer.Typer(no_args_is_help=True)
app.add_typer(tasks_app, name="tasks")
app.add_typer(service_app, name="service")
app.add_typer(maker_app, name="maker")


@app.callback()
def configure(state_dir: Path | None = None):
    """Durable OpenClaw tasks and bounded MAKER microtasks."""
    if state_dir is not None:
        os.environ["LONG_TASKS_STATE_DIR"] = str(state_dir.resolve())


class LocalTransport:
    def send(self, task, message, attachments=None):
        print(
            json.dumps({"task_id": task.id, "notification": message}, ensure_ascii=False),
            file=__import__("sys").stderr,
        )
        return True

    def last_error(self):
        return None


class RoutingTransport(LocalTransport):
    def __init__(self, openclaw_bin):
        self.remote = OpenClawCliNotificationTransport(
            openclaw_bin=openclaw_bin, channel="telegram"
        )

    def send(self, task, message, attachments=None):
        if task.notify_channel == "local":
            return super().send(task, message, attachments)
        if task.notify_channel != "telegram":
            return False
        return self.remote.send(task, message, attachments)

    def last_error(self):
        return self.remote.last_error()


def _store() -> TaskStore:
    return TaskStore()


@tasks_app.command("list")
def list_tasks() -> None:
    store = _store()
    for task in store.list_tasks():
        print(
            json.dumps(
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status.value,
                    "current_step": task.current_step_index,
                    "next_run_at": task.next_run_at.isoformat(),
                    "last_summary": task.last_summary,
                },
                ensure_ascii=False,
            )
        )


@tasks_app.command("show")
def show_task(task_id: str) -> None:
    store = _store()
    task = store.get_task(task_id)
    if task is None:
        raise typer.Exit(code=1)
    steps = store.get_steps(task_id)
    attempts = [dict(row) for row in store.list_attempts(task_id)]
    events = [dict(row) for row in store.list_events(task_id)]
    print(
        json.dumps(
            {
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status.value,
                    "goal": task.goal,
                    "current_step_index": task.current_step_index,
                    "next_run_at": task.next_run_at.isoformat(),
                    "last_summary": task.last_summary,
                    "last_error": task.last_error,
                    "waiting_prompt": task.waiting_prompt,
                    "notify_channel": task.notify_channel,
                    "notify_chat_id": task.notify_chat_id,
                    "notify_message_ref": task.notify_message_ref,
                },
                "steps": [
                    {
                        "id": step.id,
                        "step_index": step.step_index,
                        "title": step.title,
                        "kind": step.kind.value,
                        "status": step.status.value,
                        "attempt_count": step.attempt_count,
                        "max_attempts": step.max_attempts,
                        "result_summary": step.result_summary,
                        "artifact_paths": step.artifact_paths,
                    }
                    for step in steps
                ],
                "attempts": attempts,
                "events": events,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@tasks_app.command("resume")
def resume_task(task_id: str) -> None:
    store = _store()
    task = store.resume_task(task_id)
    print(
        json.dumps(
            {
                "id": task.id,
                "status": task.status.value,
                "next_run_at": task.next_run_at.isoformat(),
            },
            ensure_ascii=False,
        )
    )


@tasks_app.command("cancel")
def cancel_task(task_id: str) -> None:
    store = _store()
    task = store.cancel_task(task_id)
    print(json.dumps({"id": task.id, "status": task.status.value}, ensure_ascii=False))


@tasks_app.command("tick")
def tick(
    worker_id: str = "cli-worker",
    config: Path | None = None,
    openclaw_bin: str = "openclaw",
    send_notifications: bool = False,
) -> None:
    store = _store()
    service = RuntimeService(
        store=store,
        runner=OpenClawCliAgentRunner(config_path=config, openclaw_bin=openclaw_bin),
        notifier=RoutingTransport(openclaw_bin) if send_notifications else LocalTransport(),
    )
    scheduler = service.build_scheduler()
    results = scheduler.tick(worker_id)
    print(
        json.dumps(
            {
                "results": [
                    {
                        "task_id": r.task_id,
                        "task_status": r.task_status.value,
                        "current_step": r.current_step,
                        "did_work": r.did_work,
                        "needs_user_input": r.needs_user_input,
                        "next_run_at": r.next_run_at,
                    }
                    for r in results
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@tasks_app.command("create")
def create(
    title: str = typer.Option(...),
    goal: str = typer.Option(...),
    notify_chat_id: str = typer.Option("local"),
    notify_channel: str = typer.Option("local"),
    steps_json: str = typer.Option(
        ..., help="JSON array of {title,instructions,kind?,verification?,max_attempts?}"
    ),
    reply_message_id: str | None = typer.Option(None),
) -> None:
    store = _store()
    steps = json.loads(steps_json)
    task = create_task(
        store,
        title=title,
        goal=goal,
        steps=steps,
        notify_chat_id=notify_chat_id,
        notify_channel=notify_channel,
        reply_message_id=reply_message_id,
    )
    print(json.dumps({"id": task.id, "status": task.status.value}, ensure_ascii=False))


@tasks_app.command("resume-latest-for-chat")
def resume_latest_for_chat(
    chat_id: str = typer.Option(...), reply_text: str = typer.Option(...)
) -> None:
    store = _store()
    task_id = resume_latest_waiting_task_for_chat(store, chat_id, reply_text)
    print(json.dumps({"task_id": task_id}, ensure_ascii=False))


@service_app.command("run")
def run_service(
    worker_id: str = "scheduler",
    config: Path | None = None,
    openclaw_bin: str = "openclaw",
    sleep_seconds: int | None = None,
    send_notifications: bool = False,
) -> None:
    store = _store()
    service = RuntimeService(
        store=store,
        runner=OpenClawCliAgentRunner(config_path=config, openclaw_bin=openclaw_bin),
        notifier=RoutingTransport(openclaw_bin) if send_notifications else LocalTransport(),
    )
    service.run_forever(worker_id=worker_id, sleep_seconds=sleep_seconds)


if __name__ == "__main__":
    app()
