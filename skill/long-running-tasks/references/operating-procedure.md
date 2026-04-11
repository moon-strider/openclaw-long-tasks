# Operating procedure

Long-running tasks in this repo are a separate runtime and are not connected to `openclaw tasks`. Never operate them through `openclaw tasks`, and never use `openclaw tasks` as the mental model for this system.

## Runtime-owned execution rule

The long-running task runtime owns execution:

- classify the work as long-running
- prepare the work packet
- persist task and step state
- let the scheduler and worker perform the job
- inspect or resume only through durable runtime state

Do not hand off source-of-truth responsibility to OpenClaw CLI tasks or any other external ledger.

## Standard flow

1. Confirm that long-running flow is appropriate.
2. Define a precise work packet.
3. Create durable task and step rows in the runtime database.
4. Verify the task was persisted successfully.
5. Let the scheduler and worker do the work.
6. Inspect runtime state only when needed.
7. Deliver terminal or meaningful progress updates.

Normal operation must not rely on manual `tick`.
If a newly created task is not picked up, investigate scheduler/service health or timing instead of turning manual `tick` into part of the user flow.

## Work packet checklist

Every long-running task should include:

- objective
- repo/workdir
- exact deliverable
- constraints
- validation/checks
- output format for completion summary
- whether execution is sequential or explicitly parallel

If execution mode is not specified, default to sequential steps.
Do not infer multi-agent fanout by default.

## If task creation fails

If runtime task creation fails:

1. capture the exact error
2. inspect runtime DB and validation logic
3. diagnose schema, transition, or environment prerequisites
4. either fix the prerequisite or report the exact blocker

Do not silently fall back to doing the whole task inline unless the user explicitly agrees.
Do not silently fall back to manual `tick` as a substitute for the always-on scheduler either.

## Notifications

Notifications should be driven from durable runtime state.

Rules:

- persist waiting/blocking/completion state before send attempt
- retry unsent notifications from durable state
- deduplicate repeated waiting alerts
- prefer one concise final summary per meaningful non-noop pass

## Repo-owned SQLite runtime

The runtime should:

- treat SQLite as the domain-state store
- use task and step rows
- persist attempts and events
- encode allowed transitions explicitly
- release ownership/lease on terminal or waiting states
- persist waiting/blocking state before user notification

## Recovery and liveness standard

If a worker hangs, disconnects, crashes, or loses its live process:

- do not mark the task completed just because the worker vanished
- treat lost worker state as non-terminal until the step result is durably verified
- resume from the last durable step boundary or attempt record
- keep lease expiry and reconciliation logic active so the runtime can reclaim and continue work
- only advance to the next step after the current step's success criteria are durably satisfied

Long-running execution should be restart-tolerant:

Default orchestration mode is sequential step execution.
Only opt into parallel or multi-agent execution when the user explicitly requests it and the runtime behavior is known to be stable.


- progress must be reconstructed from durable state, not only in-memory process state
- a replacement worker should be able to resume from the latest persisted step/attempt
- terminal success requires verified completion, not absence of errors

## Completion standard

A task is only done when:

- the runtime reached terminal success
- artifacts or expected outputs exist
- completion summary is durable and user-visible through the runtime path
- no crash, disconnect, timeout, or worker disappearance was mistaken for success