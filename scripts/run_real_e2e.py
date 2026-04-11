#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from long_tasks.config import Settings
from long_tasks.models import Step, StepKind, Task, TaskStatus
from long_tasks.runtime import InMemoryNotificationSink, Scheduler, Worker
from long_tasks.storage import TaskStore
from long_tasks.utils import new_id, utcnow


class E2EExecutor:
    def execute(self, task, step, attempt_count, artifacts_dir):
        output_path = artifacts_dir / 'e2e-long-task-result.txt'
        output_path.write_text('E2E long task complete\n', encoding='utf-8')
        return {
            'summary': 'E2E runtime task complete',
            'artifacts': {'result': str(output_path)},
        }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='long-tasks-e2e-') as tmp:
        root = Path(tmp)
        settings = Settings(state_dir=root)
        store = TaskStore(settings=settings)
        now = utcnow()
        task_id = new_id()
        task = Task(
            id=task_id,
            title='e2e runtime task',
            source_session_key='test-session',
            source_channel='test',
            source_chat_id='chat',
            created_at=now,
            updated_at=now,
            status=TaskStatus.READY,
            goal='write durable artifact',
            plan={'steps': ['write file']},
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
            notify_channel='test',
            notify_chat_id='chat',
            notify_message_ref=None,
        )
        step = Step(
            id=new_id(),
            task_id=task_id,
            step_index=0,
            title='write file',
            kind=StepKind.EXECUTE,
            instructions='write output artifact',
            verification={'artifact_keys': ['result']},
            max_attempts=1,
        )
        store.create_task(task, [step])
        notifier = InMemoryNotificationSink()
        worker = Worker(store=store, executor=E2EExecutor(), notifier=notifier)
        scheduler = Scheduler(store=store, worker=worker)
        results = scheduler.tick('worker-e2e')
        artifact = settings.artifacts_dir / task.id / 'e2e-long-task-result.txt'
        payload = {
            'task_id': task.id,
            'results': [
                {
                    'task_id': item.task_id,
                    'task_status': item.task_status.value,
                    'current_step': item.current_step,
                    'did_work': item.did_work,
                    'needs_user_input': item.needs_user_input,
                    'next_run_at': item.next_run_at,
                }
                for item in results
            ],
            'artifact_exists': artifact.exists(),
            'artifact_path': str(artifact),
            'notifications': notifier.messages,
            'events': [dict(row) for row in store.list_events(task.id)],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if results and artifact.exists() else 1


if __name__ == '__main__':
    raise SystemExit(main())