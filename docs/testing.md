# Testing and reproducibility

## Deterministic tests

~~~bash
uv sync --frozen --extra dev
uv run python -m pytest --cov --cov-report=term-missing
~~~

The suite covers actual SQLite transactions, worker races, expired leases, cancellation, child-process cleanup, crash recovery, outbox failures, strict result parsing, HTTP limits, voting margins and journal resumes. Tests use temporary state directories.

The scheduler-to-MAKER integration uses an explicitly identified oracle fixture. It establishes infrastructure behavior without measuring an LLM. The ordinary runtime integration writes a real file through a scripted executor; it is not an OpenClaw test.

## Installed OpenClaw

With a local model API running:

~~~bash
uv run python scripts/check_openclaw.py \
  --openclaw-bin /absolute/path/openclaw \
  --base-url http://127.0.0.1:8000/v1 --model local-single --mode read
~~~

The script creates a temporary pinned OpenClaw configuration and a random sentinel file. OpenClaw may use only the read tool; the expected string is not in its prompt. The process must return successful status and the exact file contents.

Use `--mode text` for a basic text path. That also works with a text-only Swarm ensemble. No channels or gateway service are configured by the script.

An installed OpenClaw test against a deterministic upstream fixture checks the CLI/tool protocol. A run against llama.cpp checks both that protocol and the model's ability to use it. Their results are labelled separately in [the report](experiments.md).

## Model benchmark

~~~bash
uv run python scripts/benchmark_hanoi.py \
  --base-url http://127.0.0.1:8000/v1 --model local-single \
  --state-mode deterministic --prompt-mode phase \
  --temperature 0.7 --disks 2 3 4 --seeds 11 29 \
  --max-samples 12 --output results/hanoi
~~~

The output directory must be new, or use `--resume` with the same protocol after an interruption. Completed cases are retained without re-running them; a partially sampled case resumes its journal and charged call budget. Before inference, the script writes its protocol, prompts, seeds and settings. It then retains every declared outcome in `results.json`, raw votes and decisions in `votes.json`, and resumable state in `journal.sqlite3`.

The three modes are whole-answer, decomposed steps with red-flag retries and k=1, and MAKER with k=3. The k=1 baseline already benefits from red-flag rejection; the comparison isolates voting on top of that shared validation.

A nonzero or incomplete run is evidence to investigate, not a passing benchmark. Keep failed pilots and identify any later changes to representation, decomposition or sampling. Report full-task success separately from the number of accepted legal moves.

The checked-in evidence omits model weights, SQLite binaries, environments and dependency directories. Model provenance and replay instructions are in the experiment report.

Check the archived file hashes, protocol membership and recorded move evaluations without model calls:

~~~bash
uv run python scripts/verify_evidence.py
~~~

This checks internal consistency of the archive; it is not a cryptographic attestation that inference occurred.

For a larger deterministic persistence exercise:

~~~bash
uv run python scripts/stress_maker.py --steps 10000 --output results/stress
~~~

The stress sampler is explicitly a counter fixture, with every fifth response malformed. It closes and reopens the journal halfway through, then checks every accepted action and the checkpoint chain. Its steps must not be reported as LLM-generated moves.
