from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from .models import Task


@dataclass(slots=True)
class OpenClawCliTaskResponder:
    agent_id: str = 'main'
    openclaw_bin: str = 'openclaw'
    timeout_seconds: int = 300

    def render(self, task: Task, raw_message: str, *, final: bool = False) -> str:
        prompt = self._build_prompt(task, raw_message, final=final)
        cmd = [
            self.openclaw_bin,
            'agent',
            '--agent', self.agent_id,
            '--message', prompt,
            '--json',
            '--timeout', str(self.timeout_seconds),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds)
        if proc.returncode != 0:
            return self._truncate(raw_message, max_chars=task.final_report_max_chars if final else 1200)
        text = proc.stdout.strip()
        try:
            payload = json.loads(text)
            extracted = self._extract_text(payload)
            if extracted:
                return self._truncate(extracted, max_chars=task.final_report_max_chars if final else 1200)
        except Exception:
            pass
        return self._truncate(text or raw_message, max_chars=task.final_report_max_chars if final else 1200)

    def _truncate(self, text: str, max_chars: int = 1200) -> str:
        text = text.strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + '…'

    def _extract_text(self, payload: object) -> str | None:
        if isinstance(payload, str):
            return payload.strip() or None
        if isinstance(payload, list):
            parts = [self._extract_text(item) for item in payload]
            joined = '\n'.join(part for part in parts if part)
            return joined.strip() or None
        if not isinstance(payload, dict):
            return None

        result = payload.get('result')
        if isinstance(result, dict):
            payloads = result.get('payloads')
            if isinstance(payloads, list):
                parts: list[str] = []
                for item in payloads:
                    if isinstance(item, dict):
                        text = item.get('text')
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
                if parts:
                    return '\n'.join(parts)

        for key in ('response', 'message', 'content', 'text', 'output'):
            value = payload.get(key)
            extracted = self._extract_text(value)
            if extracted:
                return extracted

        for value in payload.values():
            extracted = self._extract_text(value)
            if extracted:
                return extracted
        return None

    def _build_prompt(self, task: Task, raw_message: str, *, final: bool = False) -> str:
        packet = {
            'task_id': task.id,
            'title': task.title,
            'status': task.status.value,
            'goal': task.goal,
            'current_step_index': task.current_step_index,
            'last_summary': task.last_summary,
            'last_error': task.last_error,
            'waiting_prompt': task.waiting_prompt,
            'raw_runtime_status_message': raw_message,
        }
        if final:
            return (
                'Rewrite the runtime status into a natural final Telegram report from the task agent itself. '
                'If the full useful answer fits within 4000 characters, return the full answer instead of a short status report. '
                'If it would exceed 4000 characters, return a compressed but still useful report and explicitly say that you can send the relevant files to Telegram. '
                'Plain text only. No JSON. No markdown tables. Packet:\n'
                + json.dumps(packet, ensure_ascii=False, indent=2)
            )
        return (
            'Rewrite the runtime status into a short natural Telegram update from the task agent itself. '
            'Hard limit: 700 characters. Plain text only. No JSON. No markdown tables. '
            'It must sound like a live LLM update, mention what was done, current status, and the next step or blocker if relevant. '
            'Do not mention internal schemas, storage, or packet structure. Packet:\n'
            + json.dumps(packet, ensure_ascii=False, indent=2)
        )
