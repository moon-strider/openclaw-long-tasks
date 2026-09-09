# Earlier local CPU experiments

Recorded on 9 September 2026. This is an exploratory, small-scale MAKER adaptation through [swarm-of-experts](https://github.com/moon-strider/swarm-of-experts), with all completed cases and interrupted pilots retained.

The [continuation study](research-hundred.md) subsequently completed a full 127-move task with a more decomposed adapter. This page preserves the earlier protocols and failures.

In this earlier campaign, the clearest partial result was **11 consecutive correct moves out of a required 15** with Qwen2.5 3B and k=3, versus a correct prefix of 2 with k=1 in the same micro-prompt case. The k=3 run stopped at its voting budget; neither run solved that task. A separate two-disk case completed all three moves with both k=1 and k=3. These observations establish a working integration and a limited local result, not reliable long-horizon reasoning or a statistically demonstrated voting advantage.

## What ran

The path was `benchmark → Swarm local-single → llama.cpp → CPU model`. No cloud-model calls were made. OpenClaw was exercised separately, including its real read tool. The Hanoi benchmark calls the model API directly, so its model-call counts do not include OpenClaw integration checks.

| Model | GGUF quantization | File bytes | Model revision |
| --- | --- | ---: | --- |
| [Qwen2.5 3B Instruct](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF) | Q4_K_M | 2,104,932,768 | `7dabda4d13d513e3e842b20f0d435c732f172cbe` |
| [Qwen3 1.7B](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF) | Q8_0 | 1,834,426,016 | `90862c4b9d2787eaed51d12237eafdfe7c5f6077` |
| [Qwen3 4B](https://huggingface.co/Qwen/Qwen3-4B-GGUF) | Q4_K_M | 2,497,280,256 | `bc640142c66e1fdd12af0bd68f40445458f3869b` |

Verified model SHA-256 values:

~~~text
qwen2.5-3b-instruct-q4_k_m.gguf
626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d
Qwen3-1.7B-Q8_0.gguf
061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a
Qwen3-4B-Q4_K_M.gguf
7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5
~~~

The runtime was llama.cpp **b10867**, commit `f3f1a8f27`, on Linux x86_64 with an Intel Xeon Platinum 8573C CPU. It used four inference threads, four batch threads, a 16,384-token context, one server slot and zero GPU layers. Thinking was disabled. Python was 3.12.14; the installed OpenClaw CLI was 2026.9.3, with Node 24.19.0. These timings describe a shared execution environment, not a dedicated hardware performance benchmark.

Launch files retain local Swarm commit identifiers. Their corresponding published trees are:

| Local commit | GitHub commit with identical source tree |
| --- | --- |
| `7de0529847714ef30776640fb2655803465b0412` | [`608468da2f3b0b92d77fa3e0840a0b07bdb9c8a1`](https://github.com/moon-strider/swarm-of-experts/commit/608468da2f3b0b92d77fa3e0840a0b07bdb9c8a1) |
| `8a9146b907bedc1fde0f8ee87afd1125ff314855` | [`3ed9651c62c2c29ecdc7a28bc9be36ce880aaad7`](https://github.com/moon-strider/swarm-of-experts/commit/3ed9651c62c2c29ecdc7a28bc9be36ce880aaad7) |

The later Swarm revision refreshes dependencies and documentation; the model-call implementation is the same. The phase Qwen2.5 protocol resumed after temporary disk exhaustion while the environment was being repaired. Finished rows were retained and interrupted reservations remained charged. Its resumed row labels elapsed time as the resume segment only. This is not a claim of bit-for-bit repeatability across the environment interruption.

## Protocol and complete results

Each protocol was saved before its first call. New variants were selected after examining earlier failures; this is an adaptive exploratory series, without a held-out confirmatory experiment. Do not pool these rows into an unbiased model accuracy estimate.

The completed protocols compare:

- **Whole:** one response containing the entire move sequence, capped at 1,024 output tokens.
- **Steps:** one move at a time, k=1, with malformed/illegal responses rejected and retried.
- **MAKER:** the same task and validator, with first-to-ahead-by-k voting at k=3 and batches of three samples.

Per-step responses were capped at 256 output tokens, with at most 12 samples per step, 1,000 reserved calls per case and temperature 0.7. Seeds start at the declared seed and advance with the reservation number. Swarm forwards seed requests; no cross-runtime determinism is promised. The single llama.cpp slot serializes work even when three client requests are outstanding.

Full-prompt and phase variants used disks 2, 3 and 4 with seeds 11 and 29. The additional 4B and micro variants used disks 3 and 4 with seed 11. Counts below are **complete task successes / declared cases**, not accepted-move counts.

| Adapter | Model | Whole | Steps, k=1 | MAKER, k=3 | Evidence |
| --- | --- | ---: | ---: | ---: | --- |
| Deterministic state, full prompt | Qwen2.5 3B | 0/6 | 0/6 | 0/6 | [results](evidence/full-qwen-two/results.json) |
| Deterministic state, full prompt | Qwen3 1.7B | 0/6 | 0/6 | 0/6 | [results](evidence/full-qwen-three/results.json) |
| Deterministic state, phase prompt | Qwen2.5 3B | 0/6 | 1/6 | 1/6 | [results](evidence/phase-qwen-two/results.json) |
| Deterministic state, phase prompt | Qwen3 1.7B | 0/6 | 0/6 | 0/6 | [results](evidence/phase-qwen-three/results.json) |
| Deterministic state, phase prompt | Qwen3 4B | 0/2 | 0/2 | 0/2 | [results](evidence/phase-qwen-four/results.json) |
| Deterministic state, micro prompt | Qwen2.5 3B | 0/2 | 0/2 | 0/2 | [results](evidence/micro-qwen-two/results.json) |

All 84 declared cases in these completed protocols are retained. Only the two three-move cases passed. The 14 finished cases from three stopped pilots are retained separately and all failed.

### The two most informative cases

| Qwen2.5 3B case | Mode | Correct prefix | Required moves | Calls | Seconds | Full task |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Two disks, phase, seed 11 | Steps | 3 | 3 | 12 | 25.439 | Passed |
| Two disks, phase, seed 11 | MAKER | 3 | 3 | 27 | 45.950 | Passed |
| Four disks, micro, seed 11 | Steps | 2 | 15 | 16 | 41.869 | Failed |
| Four disks, micro, seed 11 | MAKER | 11 | 15 | 51 | 124.983 | Failed: voting budget |

The completed three-move sequence was `[[1,0,1],[2,0,2],[1,1,2]]`, where each triple is `[disk, source, target]`. k=1 rejected nine samples before finishing; k=3 rejected eighteen. Voting cost more calls without changing success in that case.

In the four-disk micro case, k=1 accepted 15 legal moves but left disks on the wrong pegs. Its optimal prefix ended after move two. k=3 maintained the correct prefix through move eleven, then failed to reach a quorum for the next move. This is a limited positive observation about one prefix, with a failed full-task outcome. It does not establish a general reliability gain.

The micro adapter supplies the iterative phase and extracts the relevant peg/top-disk state in code. The model still chooses the move. All deterministic-state adapters apply its chosen move in code. The independent optimal-move oracle is used only for evaluation; it cannot choose, correct or veto a vote. See [adapter responsibilities](maker.md#hanoi-adapters).

### Failed pilots and limits

| Pilot | Finished / declared | Result and stopping reason |
| --- | ---: | --- |
| [Qwen3 1.7B, model-produced peg arrays](evidence/pilot-pegs-qwen-three/results.json) | 5/12 | All failed; repeated malformed or inconsistent states. Temperature 0.1, 24 samples per step. |
| [Qwen2.5 3B, more explicit peg updates](evidence/pilot-explicit-pegs-qwen-two/results.json) | 2/12 | All failed; states still disagreed with proposed moves. Same sampling budget. |
| [Qwen2.5 3B, model-produced position arrays](evidence/pilot-positions-qwen-two/results.json) | 7/18 | All failed; malformed or inconsistent positions. Temperature 0.7, 12 samples per step. |

Each pilot also retains its interrupted journal rows and sample reservations. Unfinished cases are not counted as completed failures or successes. Historical prompts are archived; the first two pilot representations are no longer the current adapter. Their exact intermediate implementation was not preserved as a separate source revision.

No million-step run, 8B run, statistical confidence bound or quantization-quality comparison was performed. The original [MAKER paper](https://arxiv.org/abs/2511.09030) uses different prompts, model, sampling and scale. These experiments cannot isolate model size, quantization, prompt design or sampling as the cause of failure. Correlated wrong answers and state-format errors are visible in the retained samples.

## Real OpenClaw integration

The installed CLI ran isolated local turns through Swarm. The read check created a random `read-…` sentinel file; its contents were absent from the prompt, and success required the exact final string.

| Backend | Text, local-single | Text, local-swarm | Real read tool | Exact read answer |
| --- | --- | --- | --- | --- |
| Qwen2.5 3B | Passed | Passed | Called, zero reported tool failures | Failed |
| Qwen3 1.7B | Passed | Passed | Called, zero reported tool failures | Failed |
| Qwen3 4B | Passed | Passed | Called, zero reported tool failures | Failed |

These rows use the phase experiment directories. The 4B model returned the random suffix but dropped `read-` and added prose. The smaller models invented or substituted final contents. An `ok` CLI envelope alone therefore does not count as task success. Original envelopes are retained beside each protocol; repeated checks accompanying the micro variant are also retained.

Separately, an actual installed OpenClaw process against a **deterministic upstream fixture** passed text and read-tool checks, including the exact random sentinel. A scheduler integration test also passed the full CLI-result → verified receipt → SQLite checkpoint → local-outbox acknowledgement path. These establish protocol and runtime behavior; they are not model reasoning benchmarks. [Controlled evidence](evidence/controlled-openclaw) contains the envelopes and test output. No Telegram messages were sent.

## Deterministic durability stress

The [stress report](evidence/deterministic-stress.json) records 10,000 completed counter steps across 50,001 sample reservations, including 10,001 malformed responses. The test closed and reopened the journal after step 5,000, checked every accepted action and verified the hash-linked checkpoint chain. Elapsed time was 42.855 seconds.

The sampler is explicitly marked `llm: false`. This is evidence about persistence, rejection and recovery under a larger journal, not 10,000 error-free LLM steps.

## Reproduce or inspect

Download a listed GGUF revision and verify its checksum. Start the pinned llama.cpp server:

~~~bash
llama-server -m /absolute/path/qwen2.5-3b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 18941 --alias local-model \
  -c 16384 -t 4 -tb 4 -np 1 -ngl 0 --jinja \
  --chat-template-kwargs '{"enable_thinking":false}' --reasoning off
~~~

In the Swarm checkout:

~~~bash
LLM_BASE_URL=http://127.0.0.1:18941/v1 LLM_MODEL=local-model \
  uv run swarm-of-experts serve --port 18942
~~~

In this checkout, run the final micro protocol:

~~~bash
uv sync --frozen --extra dev
uv run python scripts/benchmark_hanoi.py \
  --base-url http://127.0.0.1:18942/v1 --model local-single \
  --state-mode deterministic --prompt-mode micro --temperature 0.7 \
  --disks 3 4 --seeds 11 --max-samples 12 --output results/micro
~~~

For the phase protocol, use `--prompt-mode phase --disks 2 3 4 --seeds 11 29`. Use a new output directory for each model and variant. After interruption, `--resume` requires the same recorded protocol and keeps charged calls.

[The archive index](evidence/index.json) lists complete versus stopped protocols and file hashes. Each directory contains the protocol, all finished results, raw samples, accepted decisions and journal metadata. Selected llama.cpp initialization/timing lines are labelled as excerpts. Weights, SQLite binaries and installed environments are omitted. `uv run python scripts/verify_evidence.py` checks archive consistency and independently reevaluates the recorded moves without making model calls.
