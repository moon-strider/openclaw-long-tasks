from __future__ import annotations

from long_tasks.api import create_task
from long_tasks.resume import resume_latest_waiting_task_for_chat
from long_tasks.storage import TaskStore
from long_tasks.models import TaskStatus


def test_resume_latest_waiting_task_for_chat_prefers_latest_waiting(tmp_path):
    store = TaskStore()
    older = create_task(
        store,
        title='older',
        goal='older waiting task',
        steps=[{'title': 's1', 'instructions': 'do x'}],
        notify_chat_id='chat-1',
        notify_channel='telegram',
    )
    newer = create_task(
        store,
        title='newer',
        goal='newer waiting task',
        steps=[{'title': 's1', 'instructions': 'do y'}],
        notify_chat_id='chat-1',
        notify_channel='telegram',
    )
    with store.transaction() as conn:
        conn.execute("UPDATE tasks SET status='waiting_user', waiting_prompt='need older input' WHERE id=?", (older.id,))
        conn.execute("UPDATE tasks SET status='waiting_user', waiting_prompt='need newer input' WHERE id=?", (newer.id,))
    resumed_id = resume_latest_waiting_task_for_chat(store, 'chat-1', 'here is my answer')
    assert resumed_id == newer.id
    resumed = store.get_task(newer.id)
    assert resumed is not None
    assert resumed.status is TaskStatus.READY
    assert resumed.waiting_prompt is None
