"""Compare whole-answer, one-step and MAKER on a configured real model endpoint.

Run from an installed checkout; no oracle is supplied to the sampler or voter.
Every predeclared run, including failures, is retained in the output directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path

from long_tasks.hanoi import Hanoi, evaluate_moves
from long_tasks.maker import (
    Journal,
    Maker,
    MakerFailure,
    VotingConfig,
    canonical,
    digest,
    strict_json,
)
from long_tasks.sampling import HTTPSampler, SamplingConfig


def write_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


async def benchmark(args):
    args.output.mkdir(parents=True, exist_ok=args.resume)
    manifest = {
        "protocol": 2,
        "state_mode": args.state_mode,
        "prompt_mode": args.prompt_mode,
        "disks": args.disks,
        "seeds": args.seeds,
        "model": args.model,
        "base_url": args.base_url,
        "temperature": args.temperature,
        "k": args.k,
        "max_samples": args.max_samples,
        "modes": ["whole", "steps", "maker"],
        "validation": "legal_move_and_state_consistency",
        "oracle": "evaluation_only",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prompts": {
            str(n): {
                "whole": Hanoi(n, args.state_mode, args.prompt_mode).whole_prompt(),
                "initial_step": Hanoi(n, args.state_mode, args.prompt_mode).prompt(
                    Hanoi(n).initial_state
                ),
            }
            for n in args.disks
        },
    }
    protocol_path = args.output / "protocol.json"
    rows = []
    if args.resume and protocol_path.is_file():
        previous = json.loads(protocol_path.read_text())
        manifest["started_at"] = previous["started_at"]
        if manifest != previous:
            raise ValueError("Resume protocol differs from the recorded benchmark")
        if (args.output / "results.json").is_file():
            saved = json.loads((args.output / "results.json").read_text())
            if saved["protocol_sha256"] != digest(manifest):
                raise ValueError("Results belong to a different protocol")
            rows = saved["runs"]
    else:
        write_json(protocol_path, manifest)
    finished = {row["run_id"] for row in rows}
    with Journal(args.output / "journal.sqlite3") as journal:
        for disks in args.disks:
            task = Hanoi(disks, args.state_mode, args.prompt_mode)
            for seed in args.seeds:
                base = SamplingConfig(
                    base_url=args.base_url,
                    model=args.model,
                    temperature=args.temperature,
                    seed=seed,
                )
                for mode in ["whole", "steps", "maker"]:
                    started = time.monotonic()
                    run_id = f"{mode}-d{disks}-s{seed}"
                    if run_id in finished:
                        continue
                    resumed = journal.row(run_id) is not None
                    config = replace(base, max_tokens=1024 if mode == "whole" else 256)
                    sampler = HTTPSampler(config)
                    row = {
                        "run_id": run_id,
                        "mode": mode,
                        "disks": disks,
                        "seed": seed,
                        "sampling": asdict(config),
                    }
                    moves = []
                    try:
                        if mode == "whole":
                            sample = await sampler.sample(task.whole_prompt(), 0)
                            row.update(sample=asdict(sample), calls=1)
                            value = strict_json(sample.text)
                            if (
                                sample.finish_reason != "stop"
                                or not isinstance(value, dict)
                                or set(value) != {"moves"}
                                or not isinstance(value["moves"], list)
                            ):
                                raise ValueError("Invalid whole-answer response")
                            moves = value["moves"]
                        else:
                            voting = VotingConfig(
                                k=1 if mode == "steps" else args.k,
                                concurrency=1 if mode == "steps" else 3,
                                max_samples=args.max_samples,
                                max_calls=1000,
                                retain_responses=True,
                            )
                            maker = Maker(task, sampler, voting, journal)
                            try:
                                row["report"] = await maker.run(run_id)
                            except MakerFailure as exc:
                                row["error"] = str(exc)
                                row["report"] = journal.report(run_id)
                            moves = [
                                json.loads(r[0])["action"]
                                for r in journal.conn.execute(
                                    "SELECT decision FROM maker_steps WHERE run_id=? ORDER BY step",
                                    (run_id,),
                                )
                            ]
                            row["calls"] = row["report"]["calls"]
                    except (ValueError, RuntimeError) as exc:
                        row["error"] = str(exc)
                    finally:
                        await sampler.close()
                    row.update(
                        evaluation=evaluate_moves(task, moves),
                        moves=moves,
                        elapsed_seconds=round(time.monotonic() - started, 3),
                    )
                    row["passed"] = not row.get("error") and row["evaluation"]["passed"]
                    if resumed:
                        row["resumed"] = True
                        row["elapsed_resume_segment_seconds"] = row.pop("elapsed_seconds")
                    rows.append(row)
                    write_json(
                        args.output / "results.json",
                        {"protocol_sha256": digest(manifest), "runs": rows},
                    )
                    print(
                        canonical(
                            {
                                k: row.get(k)
                                for k in [
                                    "run_id",
                                    "passed",
                                    "calls",
                                    "elapsed_seconds",
                                    "evaluation",
                                    "error",
                                ]
                            }
                        ),
                        flush=True,
                    )
        evidence = {
            "samples": [
                dict(r)
                for r in journal.conn.execute("SELECT * FROM maker_samples ORDER BY run_id,number")
            ],
            "steps": [
                dict(r)
                for r in journal.conn.execute("SELECT * FROM maker_steps ORDER BY run_id,step")
            ],
        }
        write_json(args.output / "votes.json", evidence)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="local-single")
    parser.add_argument("--prompt-mode", choices=["full", "phase", "micro"], default="full")
    parser.add_argument("--state-mode", choices=["model", "deterministic"], default="model")
    parser.add_argument("--disks", nargs="+", type=int, default=[3, 4])
    parser.add_argument("--seeds", nargs="+", type=int, default=[11, 29])
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--max-samples", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    asyncio.run(benchmark(parser.parse_args()))


if __name__ == "__main__":
    main()
