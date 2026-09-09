from __future__ import annotations

from .storage import TaskStore


def resume_latest_waiting_task_for_chat(
    store: TaskStore, chat_id: str, user_reply: str
) -> str | None:
    tasks = [
        task
        for task in store.list_tasks()
        if task.notify_chat_id == chat_id and task.status.value == "waiting_user"
    ]
    if not tasks:
        return None
    tasks.sort(key=lambda t: t.updated_at, reverse=True)
    task = tasks[0]
    try:
        store.resume_task(task.id, user_reply)
    except ValueError:
        if not user_reply.strip() or len(user_reply) > 16384:
            raise
        return None
    return task.id
