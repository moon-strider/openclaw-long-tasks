# Local model continuation evidence

This archive contains real CPU model responses from adaptive pilots and closed-loop Hanoi attempts. `index.json` lists the included completed protocols and file hashes. Every declared case of an included closed-loop protocol is retained, including failures.

- `pilots/`: calibration on oracle-supplied states or isolated lookup questions; these are not completed Hanoi tasks.
- `runs/`: stateful attempts; the next prompt uses the actual accepted model move. Evaluation stops a case at its first incorrect accepted move, without correcting it.
- `runtime.json`: model revisions, checksums, CPU settings and equivalent local/published source commits.

Each run directory contains the prewritten protocol, outcomes, JSON journal export, raw call traces, source/launch metadata and the full llama.cpp startup/timing log. SQLite databases and model weights are excluded. Traces preserve exact response text and model-reported token usage. Historical exploratory scripts are text snapshots, not installed runtime modules.

Run `uv run python scripts/verify_choices.py docs/evidence/hundred` from the repository root to check hashes and replay recorded responses, candidate parsing, first-to-ahead voting, checkpoint chains and the independent Hanoi evaluation. This performs no new inference and cannot substitute for the original model calls. The live experiment command is documented in the [research report](../../research-hundred.md).
