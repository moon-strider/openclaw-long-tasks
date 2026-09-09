# Extending the local MAKER experiment

**Qwen2.5 3B Q4_K_M completed all 127 moves of seven-disk Hanoi in all four declared lookup runs: two seeds, each with k=1 and k=3, on CPU.** Every counted move required a model-selected destination or action. The earlier best result was an eleven-move prefix.

| Seed | Voting margin | Correct moves | Reserved attempts | Received completions | Elapsed seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| 941 | k=3 | 127/127 | 381 | 381 | 831.524 |
| 941 | k=1 | 127/127 | 127 | 127 | 350.343 |
| 947 | k=3 | 127/127 | 387 | 383 | 80.974, resume segment only |
| 947 | k=1 | 127/127 | 127 | 127 | 265.367 |

[All results](evidence/hundred/runs/qwen-two-lookup/results.json) include the complete sequences and final peg arrays. The selected protocol reserved **1,022 attempts and recorded 1,018 completions**. One call failed without a completion; three reservations were interrupted when the execution environment disconnected. Those four attempts remain charged. They are not counted as received model answers.

Both k=1 controls passed, so this experiment **does not establish a voting advantage for the selected adapter**. The practical improvement came with finer prompt decomposition and a simpler output representation; this series does not isolate their individual effects. The observation covers a small repeated decision task under these settings, without a statistical reliability guarantee.

The evaluator uses an independent recursive oracle. It can stop a failed run, but cannot select, repair or replace a model's move.

## Research informing the experiments

| Source | Relevant idea | Use here |
| --- | --- | --- |
| [MAKER: Solving a Million-Step LLM Task with Zero Errors](https://arxiv.org/abs/2511.09030) | Small execution steps, first-to-ahead-by-k voting, red flags; correlated wrong answers can defeat voting | Keep exact voting and charged budgets; inspect failures by state before increasing k |
| [Decomposed Prompting](https://arxiv.org/abs/2210.02406) | Different subtasks can use specialized prompts and examples | Separate disk-one routing from selection of the other legal move |
| [Self-Consistency](https://arxiv.org/abs/2203.11171) | Aggregate multiple sampled answers instead of trusting one sample | Compare k=1 with k=3 under the same selected adapter and sampling settings |
| [Grammar-Constrained Decoding](https://arxiv.org/abs/2305.13971) | Restrict output structure during generation | Require a JSON choice; the grammar does not encode the correct choice |
| [ConSol](https://arxiv.org/abs/2503.17587) | Stop sampling when sufficient consistency is reached | Report actual call counts and retain the existing sequential margin rule; this is not a ConSol implementation |

The implementation is an adaptation, not a reproduction of the papers' exact prompts, model, parser or scale. None of these papers establishes a guarantee for this quantized local model.

For interpretation, the [July 2025 Rethinking the Illusion of Thinking study](https://arxiv.org/abs/2507.01231) is also relevant: it evaluates stepwise Hanoi and agent dialogue, and reports continuing failures as complexity rises. Its agents infer more of the state and solution than this adapter. Our seven-disk, heavily decomposed run therefore cannot be compared as a model leaderboard result or a refutation of that study. [Reframing the Reasoning Cliff as an Agentic Gap](https://arxiv.org/abs/2506.18957) similarly emphasizes the effect of execution interfaces; our contribution here is a traceable local execution experiment, not a claim about intrinsic reasoning limits.

## Pilot design

Initial pilots use known correct states for calibration, with three samples at each of seven states. Later failure diagnostics use four selected states; the final lookup pilot uses three isolated routing questions. Their counts are per-call accuracy, not completed closed-loop tasks. They help identify states where the same wrong answer dominates. Prompt variants are selected adaptively, so the pilot observations are descriptive rather than a held-out accuracy estimate.

On Qwen2.5 3B Q4_K_M, the original micro prompt scored 13/21. A source/destination lookup prompt scored 7/21; adding a JSON grammar raised it to 16/21, and examples raised it to 17/21. Several errors were systematic across all three samples of a state.

Changing the output to a choice among every generally legal move scored 19/21. The remaining failures involved a negatively worded rule. Rephrasing it as choosing the option with the largest disk number scored 21/21. On the even phases of the correct trajectory, two legal actions move disk one and the remaining legal action moves another disk; the positive rule expresses the same iterative phase without computing its answer in the runtime.

Qwen3 4B Q4_K_M scored 15/21 with the initial choice prompt on both an odd-disk pilot and the first seven states of an even-disk pilot. It repeatedly preferred one choice label on disk-one moves. This result argues against interpreting model size alone as a solution.

## Further failed attempts and decomposition

The 21/21 pilot did not generalize: all four fixed-choice cases with seven disks stopped after eight correct moves. k=3 repeated the same incorrect ninth move. Qwen2.5 7B Q4_K_M, downloaded from the official two-shard GGUF revision with both checksums verified, stopped after two correct moves in all four matched cases. Larger model size did not fix this prompt.

A diagnostic pilot rotated option rows, represented routes as pairs, and requested short explanations. Those variants scored 7/12, 3/12 and 3/12. A second pilot tried numeric arrows, color names and a Python predicate: 6/12, 6/12 and 3/12. A third tested static worked examples, subtraction and a shorter prompt: 9/12, 3/12 and 6/12. Worked examples fixed the original failing state but caused an unavailable C response at the initial state. All four subsequent closed-loop example cases exhausted 48 samples without completing their first move.

The next decomposition removed action selection from the routing question. The model only reads one value from a three-entry dictionary; code supplies disk one and its current source. The other phase still asks the model to select the largest-disk action among all legal moves. An isolated pilot scored 9/9 on dictionary lookup, 9/9 on a repeating-sequence formulation and 8/9 on a word translation formulation. The dictionary variant was selected for the next closed-loop protocol. These adaptive diagnostics total **303 real model calls**.

## Complete case comparison

| Model and adapter | Full successes / cases | Correct prefix in each case | Evidence |
| --- | ---: | ---: | --- |
| Qwen2.5 3B, fixed choices | 0/4 | 8 | [results](evidence/hundred/runs/qwen-two-fixed/results.json) |
| Qwen2.5 7B, same fixed choices | 0/4 | 2 | [results](evidence/hundred/runs/qwen-seven-fixed/results.json) |
| Qwen2.5 3B, routing demonstrations | 0/4 | 0 | [results](evidence/hundred/runs/qwen-two-examples/results.json) |
| Qwen2.5 3B, isolated route lookup | 4/4 | 127 | [results](evidence/hundred/runs/qwen-two-lookup/results.json) |

All **16 completed cases**, including twelve failed attempts, are retained. Together with the 303 pilot calls, this continuation has **1,613 charged attempts and 1,609 received model completions**. These totals describe the adaptive exploration; pooling them would not estimate model accuracy.

## Recovery during actual inference

The seed-947 k=3 run had already accepted 112 correct moves when the execution environment disconnected. Its writer exited with three pending reservations. The [pre-resume journal](evidence/hundred/runs/qwen-two-lookup/journal-before-resume.json) and original server log were preserved before relaunching the same model and server settings.

Resume used the same run id, protocol fingerprint, state and call numbering. It marked those pending reservations abandoned and kept them charged, then completed the remaining fifteen moves. The replay verifier confirms that all 112 accepted checkpoint records and all previously completed sample records are unchanged. [Interruption metadata](evidence/hundred/runs/qwen-two-lookup/interruption.json) and both launch/source records are included. The reported 80.974 seconds measure only the resumed segment, not total execution time.

An earlier call in that case also failed with an empty exception message. Its original trace is preserved unchanged; the exception class was not captured, so its underlying cause is unknown. The tracer now records the class and a fallback representation, with a regression test for empty messages. No wrong answer or replacement answer is invented for a missing completion.

## Responsibility boundary

For the original choice variants, `ChoiceHanoi` enumerates every physically legal move, assigns stable labels and asks the model to select one. The lookup variant decomposes the odd phase further:

| Operation | Performed by |
| --- | --- |
| Select odd/even iterative phase | Code |
| Odd phase: select disk one and find its source | Code |
| Odd phase: evaluate the route table at that source | LLM |
| Even phase: enumerate all physically legal moves | Code |
| Even phase: choose the largest-disk move | LLM |
| Apply the selected move and maintain peg arrays | Code |
| Aggregate exact candidate votes and checkpoint | MAKER engine |
| Compare accepted moves with the recursive optimal sequence | Independent evaluator, which can stop but never repair |

The lookup parser accepts a wrong legal destination; it does not compare the answer with the dictionary. The other-phase parser accepts a wrong legal choice. Tests explicitly preserve both failure paths. The odd-phase disk selection is deterministic assistance and is not counted as a separate model step.

Every counted move requires an actual model-selected destination or action label. Code applies that selected move and carries its state into the next call. This is stronger deterministic assistance than the paper's model-produced-state setup. The task repeatedly exercises a small set of routing and comparison operations; a long correct chain does not establish general autonomous planning ability.

The action grammar always allows A, B and C; the route grammar always allows destinations 0, 1 and 2. Neither grammar depends on the correct answer. Unavailable labels and illegal self moves are rejected. Raw completions, prompts, call indices, token usage, voting decisions and checkpoints are retained. No cached answers, oracle-selected votes or deterministic answer fixtures are used in the model experiment.

## Closed-loop protocol

The selected initial protocol uses seven disks, new seeds 101 and 307, temperature 0.1, a 32-token output limit, margins k=3 and k=1, at most 48 samples per step and 4,000 reserved calls per case. The protocol is written before inference. Each seed runs k=3 followed by k=1. The subsequent worked-example protocol used seeds 701 and 709. The final lookup protocol uses seeds 941 and 947 with the same budgets; the dictionary response has its own fixed schema and uses the same global reservation-based seed sequence.

Evaluation stops a case at its first incorrect accepted move. It does not return a corrected state to the voter or restart that case with a fresh budget. Full success requires all 127 moves to match the optimal sequence and reach the final peg. A short pilot success is not counted toward that target.

The CPU backend is the same Qwen2.5 3B Q4_K_M file as before, with its SHA-256 reverified. llama.cpp b10867 uses four threads, one slot, an 8,192-token context and zero GPU layers. Calls pass through Swarm's `local-single` route. Thinking is disabled. Timing is from a shared execution environment, not a dedicated performance benchmark.

## Run it yourself

Download `qwen2.5-3b-instruct-q4_k_m.gguf` from the [official pinned revision](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/tree/7dabda4d13d513e3e842b20f0d435c732f172cbe). Its SHA-256 must be `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d`. Launch llama.cpp b10867 on CPU:

~~~bash
llama-server -m /absolute/path/qwen2.5-3b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 18941 --alias local-model \
  -c 8192 -t 4 -tb 4 -np 1 -ngl 0 --jinja --reasoning off
~~~

In the Swarm checkout:

~~~bash
LLM_BASE_URL=http://127.0.0.1:18941/v1 LLM_MODEL=local-model \
  uv run swarm-of-experts serve --port 18942
~~~

In this checkout, run a new experiment directory:

~~~bash
uv sync --frozen --extra dev
uv run python scripts/benchmark_choices.py \
  --base-url http://127.0.0.1:18942/v1 --model local-single \
  --disks 7 --routing-style lookup --rule-style positive \
  --seeds 941 947 --margins 3 1 --temperature 0.1 \
  --max-samples 48 --max-calls 4000 --output results/hundred
~~~

Use the same command with `--resume` after an interruption. It checks the protocol, retains charged calls and finished cases, and labels resumed timing as a segment. Use a fresh directory to intentionally start a new attempt. Cross-runtime seed determinism and the same outcome are not promised.

The model path is `benchmark → Swarm local-single → llama.cpp → CPU`. This continuation does not launch OpenClaw for each move; the installed OpenClaw checks remain documented in the [earlier integration report](experiments.md#real-openclaw-integration).

## Inspect the evidence

[The archive](evidence/hundred) includes all completed protocols, raw prompts and completions, token usage, votes, accepted moves, checkpoints, model hashes and full server timing logs. [Runtime provenance](evidence/hundred/runtime.json) maps local source commits to published commits with identical trees. Exploratory pilot source snapshots and every failed trial are retained.

~~~bash
uv run python scripts/verify_choices.py docs/evidence/hundred
~~~

This command checks archived hashes, recomputes pilot scores, rebuilds accepted decisions from raw model votes, checks checkpoint chains and independently evaluates the whole move sequence. It makes zero new model calls. The archived calls are the inference evidence; replay is an additional integrity check.
