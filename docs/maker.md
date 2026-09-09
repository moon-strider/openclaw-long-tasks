# MAKER microtasks

## Relationship to the paper

[Solving a Million-Step LLM Task with Zero Errors](https://arxiv.org/abs/2511.09030) was submitted on 12 November 2025. Its full run solved 20-disk Hanoi in 1,048,575 moves using gpt-4.1-mini. MAKER combines maximal agentic decomposition, first-to-ahead-by-k error correction and red-flagging. The published million-step result was not an 8B local-model run.

This repository is an independent, smaller adaptation. It adds durable journaling and resource budgets; it does not contain the authors' implementation or reproduce their scale.

## Engine contract

A `MicroTask` supplies its specification, initial state, maximum steps, prompt, candidate parser and goal predicate. A `Sampler` returns separate text samples with a finish reason and optional usage. Statistical independence is not guaranteed.

A parsed candidate consists of both an action and next state. Canonical JSON is its exact voting key. The leader wins when its count exceeds the runner-up's by `k`; an unseen runner-up has zero votes. Reaching `k` total votes is not enough when another candidate is close.

Samples run in bounded concurrent batches and are evaluated in reservation order. This avoids preferring faster responses, but can pay for surplus calls in an already-dispatched batch. It is not a fully asynchronous million-agent implementation.

Malformed, duplicate-key, non-finite, oversized, truncated and task-invalid responses are rejected. The engine does not repair JSON or ask a second model to rewrite a rejected answer. The Hanoi validator checks move legality and, in model-state mode, consistency of the proposed state. It never consults the optimal-move oracle.

A wrong legal answer may still win. Voting only helps when the correct candidate has enough probability relative to each wrong candidate. Repeated samples from the same quantized model can be strongly correlated.

## Hanoi adapters

| Mode | Model's responsibility | Deterministic responsibility |
| --- | --- | --- |
| model state + full prompt | Choose move and return every disk's new peg | Validate legality and exact state consistency |
| deterministic state + full prompt | Choose the next move from the iterative rules | Apply that move |
| deterministic state + phase prompt | Choose the move within the current iterative phase | Select odd/even phase, apply the move |
| deterministic state + micro prompt | Choose a cycle target or the legal direction between two top disks | Select the phase, extract the relevant pegs/top disks, apply the move |

The phase adapter narrows the prompt before inference. On odd moves the model follows the smallest disk's cycle; on even moves it chooses the legal direction between the other two pegs. This supplies algorithm structure, not an oracle-selected action.

The micro adapter also extracts the relevant state before inference. Odd-phase prompts contain the smallest disk's current peg and cycle; even-phase prompts contain only the two eligible pegs and their top disks or emptiness. This is stronger programmatic decomposition and must be labelled when reporting a result. Even here, a wrong legal move can win: the validator does not enforce the requested phase or consult the oracle.

These adapters are intentionally reported separately. A success with deterministic state and phase selection does not demonstrate that a model can maintain state or plan the entire solution unaided.

The recursive oracle exists only in the benchmark evaluator and deterministic test fixtures. Evaluation reports legal moves, exact optimal prefix, final state and full-task success. It does not change the votes.

## Persistence and budgets

The journal stores:

- `maker_runs`: specification fingerprint, current state, accepted-step count, reserved-call count and status.
- `maker_samples`: reservation, response status, candidate, red flag, optional archived text and reported usage.
- `maker_steps`: accepted decision, winning vote counts and a hash-linked checkpoint chain.

Calls are reserved before dispatch. Unknown responses after interruption become abandoned reservations and still count against the budget. Valid recorded votes can be replayed after a crash before checkpoint commit. An accepted decision and its new state commit together.

Resume requires the same task, sampler and voting fingerprint. Checkpoint integrity is verified before continuing. A completed run performs no additional inference. `pause_after` is an invocation control and can change without changing the experiment.

Limits cover concurrent calls, samples per step, total reserved calls, response bytes, per-step timeout and total task steps. Raw response retention is off by default; experiments enable it deliberately. A run that cannot reach a quorum becomes blocked, with no fabricated answer.

## CLI and scheduler

~~~bash
openclaw-long-tasks maker hanoi --run-id example --disks 3 \
  --base-url http://127.0.0.1:8000/v1 --model local-single \
  --state-mode deterministic --prompt-mode phase --k 3 --max-calls 200
openclaw-long-tasks maker show example
~~~

To use the same engine as a scheduler job, use `maker enqueue`. Its `steps_per_pass` bounds accepted microsteps per worker pass. A slice saves progress, returns the task to ready and releases the execution lease. The next pass resumes the same journal and budgets.

Prefer a single-generator model endpoint for voting. Swarm's natural-language merger has a different purpose and should not be substituted for an exact candidate voter.
