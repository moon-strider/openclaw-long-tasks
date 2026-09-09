"""Validate public task plans before persisting or executing them."""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .utils import stable_json

Text = Annotated[str, Field(min_length=1, max_length=16384, strict=True)]


class Contains(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Text
    contains: Text


class Rules(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_exit_code: int | None = None
    file_exists: list[Text] = Field(default_factory=list, max_length=64)
    file_contains: list[Contains] = Field(default_factory=list, max_length=64)
    json_keys: list[Text] = Field(default_factory=list, max_length=64)
    artifact_keys: list[Text] = Field(default_factory=list, max_length=64)


def validate_task(task, steps):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task.id):
        raise ValueError("Invalid task id")
    if not isinstance(task.title, str) or not task.title.strip() or len(task.title) > 512:
        raise ValueError("Title must contain 1–512 characters")
    if not isinstance(task.goal, str) or not task.goal.strip() or len(task.goal) > 16384:
        raise ValueError("Goal must contain 1–16384 characters")
    if (
        type(task.final_report_max_chars) is not int
        or not 256 <= task.final_report_max_chars <= 4000
    ):
        raise ValueError("Report limit must be 256–4000 characters")
    if not 1 <= len(steps) <= 256:
        raise ValueError("A task must have 1–256 steps")
    for index, step in enumerate(steps):
        if step.task_id != task.id or step.step_index != index:
            raise ValueError("Steps must belong to this task and be consecutively indexed")
        if not isinstance(step.title, str) or not step.title.strip() or len(step.title) > 512:
            raise ValueError("Invalid step title")
        if (
            not isinstance(step.instructions, str)
            or not step.instructions.strip()
            or len(step.instructions) > 32768
        ):
            raise ValueError("Invalid step instructions")
        if type(step.max_attempts) is not int or not 1 <= step.max_attempts <= 20:
            raise ValueError("max_attempts must be 1–20")
        Rules.model_validate(step.verification)
    if len(stable_json({"plan": task.plan, "state": task.shared_state}).encode()) > 262144:
        raise ValueError("Task metadata exceeds 256 KiB")
