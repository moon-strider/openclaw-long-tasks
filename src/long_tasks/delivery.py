from __future__ import annotations

from pathlib import Path

from .models import Attachment, Task, TaskStatus

TEXT_FILE_SUFFIXES = {'.txt', '.md', '.py', '.js', '.ts', '.c', '.pdf', '.docx'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def select_telegram_attachments(paths: list[str], max_images: int = 10, max_files: int = 10) -> list[Attachment]:
    attachments: list[Attachment] = []
    image_count = 0
    file_count = 0
    for raw_path in paths:
        path = Path(raw_path)
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES and image_count < max_images:
            attachments.append(Attachment(path=str(path), kind='image'))
            image_count += 1
        elif suffix in TEXT_FILE_SUFFIXES and file_count < max_files:
            attachments.append(Attachment(path=str(path), kind='file'))
            file_count += 1
    return attachments


def build_delivery_payload(task: Task, rendered_message: str, artifact_paths: list[str] | None = None) -> tuple[str, list[Attachment]]:
    artifact_paths = artifact_paths or []
    if task.status is not TaskStatus.COMPLETED:
        return rendered_message, []
    if len(rendered_message) <= task.final_report_max_chars:
        return rendered_message, select_telegram_attachments(artifact_paths)
    short = rendered_message[: max(0, task.final_report_max_chars - 200)].rstrip()
    short += '\n\nПолный отчёт не влез в одно сообщение. Если хочешь, я отправлю сюда релевантные файлы.'
    return short, select_telegram_attachments(artifact_paths)
