---
name: long-running-tasks
description: Operate tasks already assigned to the openclaw-long-tasks Python runtime, including durable plans, inspection, recovery, replies and MAKER microtask jobs.
---

# Long-running tasks

Use the installed `openclaw-long-tasks` CLI for this repository's tasks. Its SQLite journal owns progress; OpenClaw's separate task ledger does not describe these jobs.

Choose an explicit state directory through `LONG_TASKS_STATE_DIR` or `--state-dir`. Create ordered steps with observable verification rules, preserving the user's language and requested deliverable. Do not mark a task complete merely because an agent returned text.

## Operate a task

- Create: `openclaw-long-tasks tasks create --title ... --goal ... --steps-json ...`.
- Inspect: `tasks list` and `tasks show TASK_ID`.
- Execute one pass: `tasks tick --config /absolute/path/openclaw.json`.
- Run a service: `service run --config /absolute/path/openclaw.json`.
- Resume a waiting, blocked or failed step: `tasks resume TASK_ID`.
- Supply a waiting reply: `tasks resume-latest-for-chat --chat-id ... --reply-text ...`.
- Cancel: `tasks cancel TASK_ID`.

Use a service for ongoing work when the user wants ongoing execution. A skill invocation alone does not authorize installing a daemon, changing account settings or sending messages.

The runner uses isolated `openclaw agent exec` turns with the supplied configuration. It expects final JSON containing a nonempty summary and an optional artifact mapping. Reported files must exist inside the task workspace. The runtime creates a fresh receipt and verifies configured rules before advancing.

Execution can repeat after a crash. Make side effects idempotent and inspect the current step and attempt history before manually resuming it. Resume grants a new attempt budget; cancellation does not undo completed external effects.

## MAKER jobs

Use `maker enqueue` for a scheduler-owned Hanoi job or `maker hanoi --run-id ...` for a direct resumable run. A single-generator OpenAI-compatible endpoint, including Swarm's `local-single`, supplies samples.

Choose state and prompt modes explicitly when reporting an experiment. `--state-mode deterministic --prompt-mode phase` delegates state updates and iterative phase selection to code. It is a smaller adaptation, not an exact reproduction of the paper.

Keep the same model, seed, temperature and voting budget when resuming a run id. A blocked voting run needs diagnosis; do not silently reset its journal to obtain a more favorable result.

## Reporting

Notifications are local by default. External Telegram delivery requires an authorized target on the task and `--send-notifications` on the worker. Do not enable it merely to test task execution.

Distinguish verified task completion, successful notification delivery, controlled API fixtures and actual LLM results. Preserve failed model runs. Never describe deterministic test steps as LLM steps or extrapolate a short run to a million-step reliability claim.
