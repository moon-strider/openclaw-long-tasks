# E2E testing

## What counts as real E2E

A real end-to-end test for long-running tasks must exercise this repo's real durable runtime path.

It is **not** enough to:

- instantiate models only
- run a pure in-memory smoke scenario with no durable store
- assert on mocked scheduler decisions only
- rely on an external task ledger unrelated to this runtime's own state machine

## Minimum criteria

A valid e2e proves all of these:

- a real task row is created in the runtime database
- one or more worker passes run through the real runtime code
- task and step state transition durably
- an output artifact is produced
- deterministic verification is applied
- completion or waiting/blocking summary comes from the runtime path
- when the requested deliverable is a file or report for the human, that artifact is actually delivered through Telegram from an allowed media path, not merely left on local disk

## Preferred test shapes

### Shape A: single-process durable runtime e2e

- create task in SQLite
- run scheduler tick
- verify task/step rows changed
- verify output file
- verify completion summary

### Shape B: restart-recovery e2e

- create task
- begin execution and persist intermediate state
- simulate process death or expired lease
- run recovery path
- verify task resumes from durable boundary

### Shape C: waiting-user e2e

- create task that requests user input
- verify `waiting_user` is persisted before notification
- verify duplicate alerts are not re-sent every tick
- resume task explicitly and verify progress continues

## Failure is still useful

If a runtime-level e2e fails because of a real prerequisite, the test should capture that explicitly.

Examples:

- database unavailable
- invalid transition rejected
- lease cannot be acquired
- verifier fails expected output

That is still valuable because it proves the real runtime path and the real blocker.

## Anti-pattern

Do not call something "e2e" if it only proves that some other external task system created a record while this repo's own runtime did no substantive orchestration.