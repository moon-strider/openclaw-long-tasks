from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import Attachment, Step, Task


class AgentRunner(Protocol):
    def run_step(self, task: Task, step: Step, attempt_count: int, artifacts_dir: Path) -> dict: ...


class NotificationTransport(Protocol):
    def send(self, task: Task, message: str, attachments: list[Attachment] | None = None) -> bool: ...
    def last_error(self) -> str | None: ...


@dataclass(slots=True)
class OpenClawCliAgentRunner:
    agent_id: str = 'main'
    openclaw_bin: str = 'openclaw'
    timeout_seconds: int = 1800

    def run_step(self, task: Task, step: Step, attempt_count: int, artifacts_dir: Path) -> dict:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        output_path = artifacts_dir / f'step-{step.step_index}-output.json'
        prompt = self._build_prompt(task, step, attempt_count, artifacts_dir, output_path)
        cmd = [
            self.openclaw_bin,
            'agent',
            '--agent', self.agent_id,
            '--message', prompt,
            '--json',
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'openclaw agent failed with code {proc.returncode}')
        if not output_path.exists():
            raise RuntimeError(f'agent runner did not produce required artifact: {output_path}')
        payload = json.loads(output_path.read_text(encoding='utf-8'))
        summary = payload.get('summary') or f'Completed step {step.step_index}: {step.title}'
        artifacts = payload.get('artifacts') or {'result': str(output_path)}
        result = dict(payload)
        result['summary'] = summary
        result['artifacts'] = artifacts
        return result

    def _build_prompt(self, task: Task, step: Step, attempt_count: int, artifacts_dir: Path, output_path: Path) -> str:
        completed_steps: list[dict[str, object]] = []
        for item in task.shared_state.get('completed_steps', []):
            if isinstance(item, dict):
                completed_steps.append({
                    'step_index': item.get('step_index'),
                    'title': item.get('title'),
                    'summary': item.get('summary'),
                    'artifact_paths': item.get('artifact_paths', []),
                })

        packet = {
            'task_id': task.id,
            'task_title': task.title,
            'task_goal': task.goal,
            'task_plan': task.plan,
            'task_shared_state': task.shared_state,
            'previous_step_summaries': completed_steps,
            'previous_step_artifacts': [
                {
                    'step_index': item.get('step_index'),
                    'title': item.get('title'),
                    'artifact_paths': item.get('artifact_paths', []),
                }
                for item in completed_steps
            ],
            'current_step_index': step.step_index,
            'current_step_title': step.title,
            'current_step_kind': step.kind.value,
            'current_step_instructions': step.instructions,
            'verification': step.verification,
            'attempt_count': attempt_count,
            'artifacts_dir': str(artifacts_dir),
            'shared_artifacts_dir': str(artifacts_dir / 'shared'),
            'required_output_json_path': str(output_path),
            'required_output_schema': {
                'summary': 'string',
                'artifacts': {'result': 'absolute path string, must exist'},
                'shared_state': 'optional object with durable context for later steps',
                'command_exit_code': 'optional integer',
            },
        }
        return (
            'You are the execution runner for one long-task step. '
            'Complete exactly the requested step, create any needed artifacts, and write a JSON result file. '
            'Do not ask follow-up questions unless strictly required. '
            'Task packet:\n' + json.dumps(packet, ensure_ascii=False, indent=2)
        )


@dataclass(slots=True)
class OpenClawCliNotificationTransport:
    openclaw_bin: str = 'openclaw'
    channel: str = 'telegram'
    timeout_seconds: int = 60
    silent: bool = False
    _last_error: str | None = None

    def send(self, task: Task, message: str, attachments: list[Attachment] | None = None) -> bool:
        attachments = attachments or []
        if not self._send_one(task, message, None):
            return False
        image_batch: list[Attachment] = []
        for attachment in attachments:
            if attachment.kind == 'image':
                image_batch.append(attachment)
                if len(image_batch) == 10:
                    if not self._send_media_batch(task, image_batch):
                        return False
                    image_batch = []
            else:
                if not self._send_one(task, attachment.caption or Path(attachment.path).name, attachment.path):
                    return False
        if image_batch and not self._send_media_batch(task, image_batch):
            return False
        return True

    def _send_media_batch(self, task: Task, attachments: list[Attachment]) -> bool:
        media = ','.join(item.path for item in attachments)
        caption = attachments[0].caption or 'Отправляю релевантные изображения по задаче.'
        return self._send_one(task, caption, media)

    def _send_one(self, task: Task, message: str, media: str | None) -> bool:
        cmd = [
            self.openclaw_bin,
            'message',
            'send',
            '--channel', self.channel,
            '--target', task.notify_chat_id,
        ]
        if message:
            cmd.extend(['--message', message])
        if media:
            cmd.extend(['--media', media])
        if task.reply_message_id:
            cmd.extend(['--reply-to', task.reply_message_id])
        if self.silent:
            cmd.append('--silent')
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds)
        if proc.returncode == 0:
            self._last_error = None
            return True
        self._last_error = proc.stderr.strip() or proc.stdout.strip() or f'openclaw message send failed with code {proc.returncode}'
        return False

    def last_error(self) -> str | None:
        return self._last_error
