from __future__ import annotations

from typing import Iterable

from .models import Step, StepKind, Task, TaskStatus
from .storage import TaskStore
from .utils import new_id, utcnow


def create_task(
    store: TaskStore,
    *,
    title: str,
    goal: str,
    steps: Iterable[dict],
    notify_chat_id: str,
    notify_channel: str = 'telegram',
    source_session_key: str = 'external',
    source_channel: str = 'external',
    source_chat_id: str | None = None,
    notify_message_ref: str | None = None,
    reply_message_id: str | None = None,
    final_report_max_chars: int = 4000,
) -> Task:
    now = utcnow()
    task_id = new_id()
    step_list = list(steps)
    task = Task(
        id=task_id,
        title=title,
        source_session_key=source_session_key,
        source_channel=source_channel,
        source_chat_id=source_chat_id,
        created_at=now,
        updated_at=now,
        status=TaskStatus.READY,
        goal=goal,
        plan={'steps': [item['title'] for item in step_list]},
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
        notify_channel=notify_channel,
        notify_chat_id=notify_chat_id,
        notify_message_ref=notify_message_ref,
        reply_message_id=reply_message_id,
        final_report_max_chars=final_report_max_chars,
    )
    task_steps = [
        Step(
            id=new_id(),
            task_id=task_id,
            step_index=index,
            title=item['title'],
            kind=StepKind(item.get('kind', 'execute')),
            instructions=item['instructions'],
            verification=item.get('verification', {}),
            max_attempts=item.get('max_attempts', 3),
        )
        for index, item in enumerate(step_list)
    ]
    store.create_task(task, task_steps)
    created = store.get_task(task_id)
    assert created is not None
    return created