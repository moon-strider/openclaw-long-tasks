"""Closed-loop Hanoi experiments using actual model choices and exact voting."""

import argparse
import asyncio
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

from benchmark_hanoi import write_json

from long_tasks.hanoi import Hanoi, evaluate_moves
from long_tasks.hanoi_choices import CHOICE_FORMAT, DESTINATION_FORMAT, ChoiceHanoi
from long_tasks.maker import Journal, Maker, MakerFailure, VotingConfig, digest
from long_tasks.sampling import HTTPSampler, SamplingConfig


class TracedSampler(HTTPSampler):
    def __init__(self, config, path, routing_style="mapping"):
        super().__init__(config)
        self.path = path
        self.routing = (
            HTTPSampler(replace(config, response_format=DESTINATION_FORMAT))
            if routing_style == "lookup"
            else None
        )

    @property
    def identity(self):
        identity = super().identity
        if self.routing is not None:
            identity["routing_sampler"] = self.routing.identity
        return identity

    async def close(self):
        if self.routing is not None:
            await self.routing.close()
        await super().close()

    async def sample(self, prompt, index):
        started = time.monotonic()
        record = {"number": index, "prompt": prompt}
        try:
            if self.routing is not None and prompt.startswith("What is the value of d["):
                record["response_format"] = DESTINATION_FORMAT
                sample = await self.routing.sample(prompt, index)
            else:
                record["response_format"] = CHOICE_FORMAT
                sample = await super().sample(prompt, index)
            record["sample"] = asdict(sample)
            return sample
        except Exception as exc:
            record["error"] = str(exc)
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            with self.path.open("a") as handle:
                handle.write(json.dumps(record) + "\n")


async def benchmark(args):
    args.output.mkdir(parents=True, exist_ok=args.resume)
    task = ChoiceHanoi(args.disks, args.rule_style, args.routing_style)
    sampling = SamplingConfig(
        base_url=args.base_url,
        model=args.model,
        temperature=args.temperature,
        max_tokens=32,
        response_format=CHOICE_FORMAT,
    )
    protocol = {
        "protocol": "closed-loop-choices-vone",
        "task": task.specification,
        "sampling": asdict(sampling),
        "seeds": args.seeds,
        "margins": args.margins,
        "max_samples": args.max_samples,
        "max_calls": args.max_calls,
        "oracle": "evaluation only; stop on first accepted incorrect move, never repair",
        "state": "apply the model-selected legal move in code",
        "required_steps": task.total_steps,
    }
    protocol_path = args.output / "protocol.json"
    if args.resume and protocol_path.exists():
        if json.loads(protocol_path.read_text()) != protocol:
            raise ValueError("Resume protocol changed")
    else:
        write_json(protocol_path, protocol)
    results_path = args.output / "results.json"
    rows = (
        json.loads(results_path.read_text())["runs"]
        if args.resume and results_path.exists()
        else []
    )
    finished = {r["run_id"] for r in rows}
    with Journal(args.output / "journal.sqlite3") as journal:
        for seed in args.seeds:
            for k in args.margins:
                run_id = f"choices-d{args.disks}-k{k}-s{seed}"
                if run_id in finished:
                    continue
                config = SamplingConfig(**{**asdict(sampling), "seed": seed})
                sampler = TracedSampler(
                    config, args.output / f"{run_id}-calls.jsonl", args.routing_style
                )
                voting = VotingConfig(
                    k=k,
                    concurrency=1 if k == 1 else 3,
                    max_samples=args.max_samples,
                    max_calls=args.max_calls,
                    retain_responses=True,
                )
                maker = Maker(task, sampler, voting, journal)
                resumed = journal.row(run_id) is not None
                start = time.monotonic()
                error = None
                try:
                    while True:
                        report = await maker.run(run_id, pause_after=1)
                        moves = [
                            json.loads(r[0])["action"]
                            for r in journal.conn.execute(
                                "SELECT decision FROM maker_steps WHERE run_id=? ORDER BY step",
                                (run_id,),
                            )
                        ]
                        evaluation = evaluate_moves(Hanoi(args.disks), moves)
                        progress = {
                            "run_id": run_id,
                            "steps": len(moves),
                            "correct_prefix": evaluation["correct_prefix"],
                            "calls": report["calls"],
                            "required_steps": task.total_steps,
                        }
                        write_json(args.output / "progress.json", progress)
                        if (
                            len(moves) % 16 == 0
                            or evaluation["correct_prefix"] != len(moves)
                            or report["status"] == "completed"
                        ):
                            print(json.dumps(progress), flush=True)
                        if evaluation["correct_prefix"] != len(moves):
                            error = "Evaluator stopped after the first incorrect accepted move"
                            journal.status(run_id, "blocked", error)
                            break
                        if report["status"] == "completed":
                            break
                except MakerFailure as exc:
                    error = str(exc)
                finally:
                    await sampler.close()
                report = journal.report(run_id)
                moves = [
                    json.loads(r[0])["action"]
                    for r in journal.conn.execute(
                        "SELECT decision FROM maker_steps WHERE run_id=? ORDER BY step", (run_id,)
                    )
                ]
                evaluation = evaluate_moves(Hanoi(args.disks), moves)
                row = {
                    "run_id": run_id,
                    "seed": seed,
                    "k": k,
                    "report": report,
                    "moves": moves,
                    "evaluation": evaluation,
                    "error": error,
                    "passed": error is None and evaluation["passed"],
                    "resumed": resumed,
                    "elapsed_seconds": None if resumed else round(time.monotonic() - start, 3),
                    "elapsed_resume_segment_seconds": (
                        round(time.monotonic() - start, 3) if resumed else None
                    ),
                }
                rows.append(row)
                write_json(results_path, {"protocol_sha256": digest(protocol), "runs": rows})
                write_json(
                    args.output / "journal.json",
                    {
                        key: [dict(r) for r in journal.conn.execute(f"SELECT * FROM {table}")]
                        for key, table in [
                            ("runs", "maker_runs"),
                            ("samples", "maker_samples"),
                            ("steps", "maker_steps"),
                        ]
                    },
                )
                print(
                    json.dumps(
                        {
                            "finished": run_id,
                            "passed": row["passed"],
                            "correct_prefix": evaluation["correct_prefix"],
                            "calls": report["calls"],
                            "error": error,
                        }
                    ),
                    flush=True,
                )


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="local-single")
    parser.add_argument("--disks", type=int, default=7)
    parser.add_argument("--seeds", type=int, nargs="+", default=[101, 307])
    parser.add_argument("--margins", type=int, nargs="+", default=[3, 1])
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--rule-style", choices=["negative", "positive"], default="positive")
    parser.add_argument(
        "--routing-style", choices=["mapping", "examples", "lookup"], default="mapping"
    )
    parser.add_argument("--max-samples", type=int, default=48)
    parser.add_argument("--max-calls", type=int, default=4000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(benchmark(arguments()))
