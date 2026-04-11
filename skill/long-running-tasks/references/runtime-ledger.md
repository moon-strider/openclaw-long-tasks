# Runtime ledger

This runtime ledger is repo-owned and separate from `openclaw tasks`. If you are inspecting long-running-task state, inspect this ledger, not OpenClaw's built-in task ledger.

This repo provides its own durable runtime ledger for long-running work.

## Storage

The source of truth is the runtime-managed SQLite database plus the artifact directory owned by this repo.

## Core records

Typical durable records are:

- `tasks`
- `steps`
- `attempts`
- `events`

## Meaning

This runtime ledger is the domain workflow engine.

It tells you:

- what long-running work exists
- which step is current
- whether the task is ready, running, waiting, blocked, completed, failed, or cancelled
- which attempts happened
- what summaries, artifacts, and errors were persisted
- whether a lease is active or reclaimable

## Operational implication

When the user asks for a long-running task, the correct first move is to create durable work through this runtime's own storage model.

Do not treat OpenClaw's built-in background task records, if any, as the source of truth for this repo's domain progress.

## Separation rule

Keep these concerns separate:

- runtime-owned SQLite ledger = domain progress and recovery
- external host/orchestrator = optional launcher or transport only

If the external host disappears, the runtime design should still make sense and remain testable as a self-contained system.