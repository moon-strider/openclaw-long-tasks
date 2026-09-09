from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import Attachment, Step, Task


class AgentRunner(Protocol):
    def run_step(self, task: Task, step: Step, attempt_count: int, artifacts_dir: Path) -> dict: ...


class NotificationTransport(Protocol):
    def send(
        self, task: Task, message: str, attachments: list[Attachment] | None = None
    ) -> bool: ...
    def last_error(self) -> str | None: ...


@dataclass(slots=True)
class OpenClawCliAgentRunner:
    config_path: Path | None = None
    openclaw_bin: str = "openclaw"
    timeout_seconds: int = 600
    model: str | None = None

    def run_step(self, task: Task, step: Step, attempt_count: int, artifacts_dir: Path) -> dict:
        from .execution import run_process
        from .maker import strict_json

        if self.config_path is None or not self.config_path.is_file():
            raise ValueError("Pass an existing pinned OpenClaw config file")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        output_path = artifacts_dir / "result.json"
        if output_path.exists():
            raise ValueError("Attempt result already exists; use a fresh attempt directory")
        packet = {
            "goal": task.goal,
            "step": step.instructions,
            "attempt": attempt_count,
            "shared_state": task.shared_state,
            "verification": step.verification,
            "artifacts_dir": str(artifacts_dir.resolve()),
        }
        prompt = (
            "Execute exactly this task step. Use tools when the task requires them. "
            'Your final response MUST be a JSON object with a nonempty string "summary", '
            'an "artifacts" object mapping names to existing absolute file paths, '
            'and optionally a "shared_state" object. Use an empty artifacts object if none '
            "were created. No markdown fences or text outside JSON. "
            "If user input is required, add status=waiting_user and put the question in summary; "
            "if execution cannot continue, add status=blocked and explain in summary. Task: "
            + json.dumps(packet, ensure_ascii=False)
            + " /no_think"
        )
        cmd = [
            self.openclaw_bin,
            "agent",
            "exec",
            "--config",
            str(self.config_path.resolve()),
            "--cwd",
            str(artifacts_dir.parent.resolve()),
            "--message-file",
            "-",
            "--json",
            "--thinking",
            "off",
            "--code-mode",
            "direct",
            "--local-model-lean",
            "--timeout",
            str(self.timeout_seconds),
        ]
        if self.model:
            cmd += ["--model", self.model]
        raw = run_process(
            cmd, prompt=prompt, cwd=artifacts_dir.parent, timeout=self.timeout_seconds + 10
        )
        envelope = strict_json(raw)
        if (
            not isinstance(envelope, dict)
            or envelope.get("ok") is not True
            or envelope.get("status") != "ok"
        ):
            raise RuntimeError("OpenClaw reported an unsuccessful agent turn")
        final = envelope.get("final")
        if not isinstance(final, str):
            raise ValueError("OpenClaw returned no final text")
        payload = strict_json(final)
        if not isinstance(payload, dict) or set(payload) - {
            "summary",
            "artifacts",
            "shared_state",
            "command_exit_code",
            "status",
        }:
            raise ValueError("Invalid agent result schema")
        if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
            raise ValueError("Agent result requires a summary")
        status = payload.get("status", "completed")
        if status == "waiting_user":
            from .runtime import NeedsUserInput

            raise NeedsUserInput(payload["summary"])
        if status == "blocked":
            from .runtime import TaskBlocked

            raise TaskBlocked(payload["summary"])
        if status != "completed":
            raise ValueError("Unknown agent result status")
        artifacts = payload.get("artifacts", {})
        if not isinstance(artifacts, dict) or len(artifacts) > 63:
            raise ValueError("Invalid artifact mapping")
        workspace = artifacts_dir.parent.resolve()
        for path in artifacts.values():
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise ValueError("Artifact paths must be absolute")
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(workspace) or not resolved.is_file():
                raise ValueError("Artifact must exist inside the task workspace")
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {**payload, "artifacts": {**artifacts, "receipt": str(output_path.resolve())}}


@dataclass(slots=True)
class OpenClawCliNotificationTransport:
    openclaw_bin: str = "openclaw"
    channel: str = "telegram"
    timeout_seconds: int = 60
    silent: bool = False
    _last_error: str | None = None

    def send(self, task: Task, message: str, attachments: list[Attachment] | None = None) -> bool:
        attachments = attachments or []
        if not self._send_one(task, message, None):
            return False
        for attachment in attachments:
            if not Path(attachment.path).is_file():
                self._last_error = "Attachment no longer exists"
                return False
            if not self._send_one(
                task, attachment.caption or Path(attachment.path).name, attachment.path
            ):
                return False
        return True

    def _send_one(self, task: Task, message: str, media: str | None) -> bool:
        cmd = [
            self.openclaw_bin,
            "message",
            "send",
            "--channel",
            self.channel,
            "--target",
            task.notify_chat_id,
        ]
        if message:
            cmd.extend(["--message", message])
        if media:
            cmd.extend(["--media", media])
        if task.reply_message_id:
            cmd.extend(["--reply-to", task.reply_message_id])
        if self.silent:
            cmd.append("--silent")
        from .execution import run_process

        try:
            run_process(cmd, timeout=self.timeout_seconds, max_bytes=65536)
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            self._last_error = type(exc).__name__
            return False
        self._last_error = None
        return True

    def last_error(self) -> str | None:
        return self._last_error
