# openclaw-long-tasks

Durable task execution for OpenClaw, plus a bounded, resumable MAKER-inspired microtask engine.

A task is an ordered plan stored in SQLite. Workers claim one step, execute it, verify its result and checkpoint progress. A separate outbox handles notification delivery. The same scheduler can run a MAKER job in short slices, releasing its lease between slices.

## Start with a local model

Requires Python 3.11+ on Linux or macOS. Use a local disk for the SQLite journal.

~~~bash
git clone https://github.com/moon-strider/openclaw-long-tasks
cd openclaw-long-tasks
uv sync --frozen --extra dev
export LONG_TASKS_STATE_DIR="$PWD/state"
~~~

Start an OpenAI-compatible model endpoint. [swarm-of-experts](https://github.com/moon-strider/swarm-of-experts) can provide that endpoint over llama.cpp and exposes a `local-single` route.

~~~bash
uv run openclaw-long-tasks maker enqueue \
  --disks 3 --base-url http://127.0.0.1:8000/v1 --model local-single \
  --state-mode deterministic --prompt-mode phase \
  --steps-per-pass 2 --max-calls 200

uv run openclaw-long-tasks tasks tick
uv run openclaw-long-tasks tasks list
uv run openclaw-long-tasks service run
~~~

The last command keeps processing due work. Stop it with Ctrl-C; a later process recovers unfinished work from the journal. Notifications stay local unless explicitly enabled.

For a direct experiment without the macro-task scheduler:

~~~bash
uv run openclaw-long-tasks maker hanoi --run-id local-hanoi \
  --disks 3 --state-mode deterministic --prompt-mode phase \
  --max-calls 200 --pause-after 2
~~~

Run the same command again to continue another slice, or omit `--pause-after` to finish. Keep the same run id, model, sampling and voting configuration. A mismatched resume is rejected.

## Run OpenClaw agent steps

Install OpenClaw separately; the recorded CLI integration uses version 2026.9.3. Save a pinned configuration such as [examples/openclaw.json](examples/openclaw.json).

~~~bash
uv run openclaw-long-tasks tasks create \
  --title 'Review a brief' --goal 'Produce a concise review' \
  --steps-json '[{"title":"Review","instructions":"Review the brief in the task workspace. Return a concise summary.","verification":{"json_keys":["summary"]}}]'

uv run openclaw-long-tasks tasks tick --config examples/openclaw.json
~~~

The runner uses isolated `openclaw agent exec` turns, an explicit workspace, stdin prompts and a bounded process deadline. It requires a valid JSON final response and writes a fresh result receipt. Model-reported artifacts must exist inside that task's workspace.

To install the CLI for everyday use, run `uv tool install .`. [Operations](docs/operations.md) covers task inspection, replies, retries, service setup and optional Telegram delivery.

## What is durable

~~~mermaid
flowchart TD
    P["Ordered plan"] --> R["Task runtime"]
    R <--> D["SQLite journal"]
    R --> O["OpenClaw agent"]
    R --> M["MAKER microtasks"]
    M <--> D
    O --> S["Swarm or model API"]
    M --> S
~~~

- Per-invocation lease tokens, heartbeats and rejection of stale worker results.
- Recovery of an actual `in_progress` step after an interrupted attempt.
- Explicit retry budgets, backoff, waiting for a reply, manual resume and cancellation.
- One transaction for the verified step, its next state, event and notification intent.
- Claimed outbox delivery with heartbeats, backoff and a finite retry limit.
- MAKER sample reservations, voting decisions and checkpoints that survive a restart.

Leases fence database commits. They cannot undo an external side effect that happened before a crash; design side-effecting steps to be idempotent. This is a single-host runtime, and OpenClaw tools run with the configured process permissions.

## MAKER and measured results

The microtask engine implements exact candidate voting with a first-to-ahead-by-k margin, red-flag rejection, bounded sampling and durable checkpoints. Its Hanoi adapters support both model-produced state and deterministic state transitions.

On CPU, Qwen2.5 3B Q4_K_M produced an **11-move correct prefix out of 15** with k=3 in one micro-prompt case, compared with a prefix of 2 for k=1. Both full tasks failed. A separate three-move task completed with both settings. The adapter selects the iterative phase and handles state updates in code; these are exploratory observations, with all failed cases retained.

A separate deterministic stress run completed 10,000 journalled counter steps across 50,001 sample reservations and a restart. Its sampler is a fixture, so these are not LLM-generated steps.

[Experiment results](docs/experiments.md) distinguish actual CPU inference, controlled OpenClaw integration tests and deterministic infrastructure tests. This repository does not claim to reproduce a million-step LLM run. [MAKER design](docs/maker.md) explains the differences from the paper and why correlated mistakes can still win.

Swarm supplies model calls; this repository owns the experiment, task semantics and recovery. Neither project requires the other for its core API.

## Development

~~~bash
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run python -m pytest --cov --cov-report=term-missing
uv build
~~~

Normal tests use temporary state directories and need no model, OpenClaw installation, provider account or outbound messaging. A separate integration job exercises an installed OpenClaw CLI against a deterministic upstream fixture. See [testing](docs/testing.md) for real-model commands.

MIT licensed. The API is still evolving; [migration notes](docs/operations.md#migrating-from-the-prototype) describe the changes from the original prototype.
