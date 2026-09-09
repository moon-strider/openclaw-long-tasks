# Operations

## State and task lifecycle

Set `LONG_TASKS_STATE_DIR` or the CLI's global `--state-dir`. The default is `~/.openclaw/tasks`. This directory contains `long-tasks.sqlite` and `artifacts/<task-id>/<attempt-id>/`.

The repository owns this journal. OpenClaw's own task ledger is not consulted. A task has 1–256 ordered steps, and only its current step is runnable.

~~~bash
openclaw-long-tasks --state-dir ./state tasks list
openclaw-long-tasks --state-dir ./state tasks show TASK_ID
openclaw-long-tasks --state-dir ./state tasks resume TASK_ID
openclaw-long-tasks --state-dir ./state tasks cancel TASK_ID
~~~

A waiting, blocked or failed task can be explicitly resumed. This resets the current unfinished step to pending and grants a new attempt budget; old attempts remain in the journal. A completed or cancelled task cannot be resumed.

For a reply to the latest waiting task in one chat:

~~~bash
openclaw-long-tasks tasks resume-latest-for-chat \
  --chat-id CHAT_ID --reply-text 'Use the shorter version'
~~~

The original waiting prompt and supplied reply become durable shared state for the next runner invocation. Invalid or raced resumes do not silently change a running task.

## Execution and verification

A normal step calls `openclaw agent exec` with an explicit `--config`. OpenClaw is a separately installed dependency; the runner does not discover ambient agents or modify account configuration.

The final assistant text must be a JSON object with a nonempty `summary`, an optional `artifacts` mapping and optional `shared_state`. A `command_exit_code` field is also accepted. To request a reply, return `status: "waiting_user"` and the question in `summary`; `status: "blocked"` records a blocker. The default status is `completed`. Markdown fences, unknown fields, missing files and paths outside the task workspace are rejected. Each attempt gets a fresh receipt.

Supported verification rules:

| Rule | Check |
| --- | --- |
| json_keys | Required keys exist in the result object |
| artifact_keys | Required named artifacts exist |
| file_exists | Named regular files exist |
| file_contains | UTF-8 file contains the specified text; one MiB read limit |
| command_exit_code | Reported integer equals the configured integer |

Unknown rule names are errors. A reported command exit code is a model assertion, not proof that an operating-system command ran. Choose checks that establish the actual deliverable: inspect file contents, parse the output or use a trusted custom verifier. A receipt alone proves that a structured result was returned.

Put input files in `artifacts/<task-id>/` before running the task. The runner supplies recent verified summaries, artifact paths and durable shared state to later steps. Full history remains in SQLite; the prompt-facing summary window retains the latest 32 completed steps.

## Leases, retries and shutdown

The default execution lease lasts 600 seconds and refreshes every 60 seconds. Every invocation has a unique lease token, including invocations using the same worker name. A worker whose lease expired or was cancelled cannot commit a step result.

A scheduler pass recovers interrupted steps and processes at most 100 due tasks. Steps execute sequentially. Multiple local worker processes may claim different tasks through SQLite; a given MAKER journal permits only one MAKER writer at a time.

Ordinary failure retries use 1, 5, 15 and then 30 minute delays, bounded by the step's `max_attempts`. Interrupted attempts consume budget. MAKER slice yields do not consume retry budget. A MAKER voting-budget failure blocks the macro task and requires investigation instead of an automatic fresh voting budget.

Child process groups are stopped on timeout or lease cancellation, and captured output is bounded. This is process ownership, not a filesystem or network sandbox. Custom in-process executors must honor cancellation themselves; their late results are still fenced.

Run the foreground service under the supervisor you already use:

~~~bash
openclaw-long-tasks --state-dir /absolute/path/state service run \
  --config /absolute/path/openclaw.json --worker-id scheduler
~~~

No installer starts a persistent daemon automatically. Keep the service's working directory, executable and state directory explicit.

## Notifications

By default, notifications are written to stderr and acknowledged locally. Outbox rows include the task snapshot from the committing step, so retries do not accidentally describe a later step.

External delivery requires both a Telegram target on the task and `--send-notifications` on the worker/service. Local tasks continue to use local delivery even when that flag is enabled. Configure and authorize the OpenClaw channel separately.

Delivery claims have heartbeats. Failed sends back off and become `dead` after eight attempts. Inspect `notifications` in SQLite for delivery status. A failed notification never retries an already-completed execution step.

Delivery is at least once. A crash after the transport sent a message but before its acknowledgement was saved can cause a duplicate; a failed attachment can also cause earlier parts to be resent. No exactly-once external-delivery guarantee is made. No Telegram messages were sent during the repository's tests.

## Backups

Keep the database and artifacts together on a local filesystem. Use SQLite's backup API or stop workers before copying the database; a live WAL database must not be backed up by copying only its main file. Restore to a separate directory first and inspect it before resuming execution.
