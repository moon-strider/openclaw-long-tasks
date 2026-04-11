---
name: long-running-tasks
description: Durable long-running task workflow for an autonomous runtime with OpenClaw allowed only as transport for agent-runner launches and user notifications. Use when work should outlive one prompt, needs retry/wait/recovery semantics, must survive restart, or should report back only on meaningful state changes.
---

# Long Running Tasks

Use this skill when the job is too big, too slow, too failure-prone, or too interruptible for one normal chat turn.

## Hard boundary

The long-task runtime is autonomous.

These long-running tasks are absolutely not related to `openclaw tasks`.
Do not use `openclaw tasks` as the runtime, the inspection surface, the operator workflow, or even the mental model for this system unless you are explicitly debugging OpenClaw's own built-in task ledger.

OpenClaw may be used only for:

- launching agent runners
- sending user-visible notifications/messages

OpenClaw must **not** be the source of truth for:

- task state
- step state
- retries
- scheduler ownership
- recovery logic
- domain workflow progress

Do not use OpenClaw built-in task ledger or `openclaw tasks` as the long-task runtime.
This is a strict rule, not a suggestion.

## Source of truth

Use the repo-owned SQLite database and artifact directory as the operational source of truth.

Keep these layers separate:

- repo-local SQLite/task tables = domain workflow state machine
- OpenClaw CLI = transport only

## Standard operating flow

### 1. Create a durable task

Create a real task row in the runtime store with:

- title
- goal
- ordered steps
- verification rules
- notify target metadata
- retry limits

Tasks and step instructions must be created in the same language the user used for the request.
Do not silently translate task titles, goals, or step instructions into English unless the user explicitly asked for that.

By default, steps must run sequentially, one after another.
Do not assume parallel or multi-agent orchestration unless the user explicitly asks for it and the runtime supports it safely.
Current practical rule: prefer sequential execution by default because multi-agent parallel behavior is not yet reliable.

Preferred entrypoint:

- `openclaw-long-tasks tasks create --title ... --goal ... --notify-chat-id ... --steps-json ...`

If the user says something like "create a task", do not execute the job inline in the same turn just because it is possible.
Persist the task and let the scheduler own execution.

### 2. Keep the scheduler always on

The scheduler must run continuously.

Preferred service mode:

- `openclaw-long-tasks service run --agent-id main`

Manual `tick` is not part of the normal user flow.
If the user asked to create a long-running task, create it durably and let the always-on scheduler pick it up.
Use manual `tick` only for explicit debugging or investigation, not to compensate for scheduler timing.

Preferred install path on Linux user session:

- `scripts/install_service.sh`

The scheduler is responsible for:

- selecting runnable tasks
- acquiring leases
- recovering expired running tasks
- retrying pending notifications
- invoking one worker pass per runnable task

For multi-step tasks, the default expectation is sequential progression through steps.
Do not fan out multiple steps in parallel unless that behavior is explicitly designed, implemented, and requested.

## Runner contract

Worker passes must call a real runner adapter.

Allowed OpenClaw usage here:

- `openclaw agent --agent <id> --message ... --json`

That CLI call is only a transport to invoke a runner agent.
It is **not** the durable owner of the task.

The runner must receive a full task packet including:

- task id/title/goal
- current step index/title/kind/instructions
- verification rules
- artifact directory
- required output JSON path/schema

The runner must write a JSON result artifact that the runtime verifies.

## Notification contract

Notifications must be driven from durable runtime state.

Allowed OpenClaw usage here:

- `openclaw message send --channel telegram --target <chat-id> --message <text>`
- `openclaw agent --agent <id> --message ...` to generate the user-facing wording for the update

That CLI usage is transport plus wording generation only.
The runtime must persist notification intent before sending and retry failed sends later.
The final user-facing update must be a short natural LLM-authored message, not a raw diagnostic template.
Keep delivery text short enough for Telegram and persist send failures with real error text.

## Waiting-user resume contract

If a task enters `waiting_user`, the runtime must preserve that state durably and support resuming from a real user reply.

Minimum requirement:

- map an incoming user reply in the same chat to the latest waiting task for that chat
- store the reply as an event
- clear `waiting_prompt`
- transition the task back to `ready`
- let the scheduler continue autonomously

## Delivery rules

Send user-visible updates when:

- task accepted
- meaningful step/pass completed
- waiting on user input
- blocked
- failed
- completed

For the final completed message:

- if the useful final answer fits in one Telegram message, send the full answer instead of a short status summary
- if it does not fit, send a compressed but still useful final report
- in that oversized case, explicitly offer to send relevant files to the same Telegram chat
- relevant artifact files may be sent as attachments, especially `.txt`, `.pdf`, `.md`, `.docx`, `.py`, `.js`, `.ts`, `.c`, and images
- if the requested deliverable is itself a report, file, or artifact for the human, the runtime must send that artifact to Telegram; having it only on local disk does not count as delivery

Do not spam for:

- scheduler ticks
- lease refreshes
- no-op polling
- repeated waiting alerts

## Inspection commands

Useful runtime commands:

- `openclaw-long-tasks tasks list`
- `openclaw-long-tasks tasks show <id>`
- `openclaw-long-tasks tasks resume <id>`
- `openclaw-long-tasks tasks cancel <id>`
- `openclaw-long-tasks tasks tick --agent-id main`
- `openclaw-long-tasks service run --agent-id main`

## E2E standard

A valid end-to-end flow must prove all of these:

- runtime task row exists in repo-owned SQLite
- scheduler process is actually running
- worker pass invokes a real runner through the allowed transport path
- artifact is produced and verified
- notification intent is stored durably
- user-visible message is sent to Telegram through the allowed transport path
- failure of message delivery is retried later from runtime state

## Anti-patterns

Do not:

- assume parallel multi-agent execution by default

- use `openclaw tasks` as state store
- rely on OpenClaw detached task records for domain progress
- keep the scheduler as a manual `tick` habit only
- keep notifications in memory only
- let runner state live only in chat history

## Read next

- `references/runtime-ledger.md`
- `references/operating-procedure.md`
- `references/e2e-testing.md`