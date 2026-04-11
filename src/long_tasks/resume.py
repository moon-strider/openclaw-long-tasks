from __future__ import annotations

from .storage import TaskStore
from .utils import utcnow


def resume_latest_waiting_task_for_chat(store: TaskStore, chat_id: str, user_reply: str) -> str | None:
    tasks = [
        task for task in store.list_tasks()
        if task.notify_chat_id == chat_id and task.status.value == 'waiting_user'
    ]
    if not tasks:
        return None
    tasks.sort(key=lambda t: t.updated_at, reverse=True)
    task = tasks[0]
    with store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'ready', waiting_prompt = NULL, last_error = NULL, next_run_at = ?, updated_at = ? WHERE id = ?",
            (utcnow().isoformat(), utcnow().isoformat(), task.id),
        )
        store.add_event(conn, 'resume-' + task.id + '-' + utcnow().isoformat(), task.id, 'task.resumed_by_user_reply', {'reply': user_reply})
    return task.id