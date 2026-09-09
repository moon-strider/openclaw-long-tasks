"""Deterministic journal stress and restart check. This does not run an LLM."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from long_tasks.maker import Candidate, Journal, Maker, Sample, VotingConfig, strict_json


class CounterTask:
    def __init__(self, steps):
        self.total_steps = steps

    @property
    def specification(self):
        return {"task": "deterministic_counter_fixture", "steps": self.total_steps}

    initial_state = 0

    def prompt(self, state):
        return str(state)

    def parse(self, text, state):
        value = strict_json(text)
        if type(value) is not int:
            raise ValueError("Expected an integer")
        return Candidate(value, value)

    def completed(self, state):
        return state == self.total_steps


class Fixture:
    identity = {"implementation": "deterministic_stress_fixture", "llm": False}

    async def sample(self, prompt, index):
        return Sample("malformed" if index % 5 == 0 else str(int(prompt) + 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=10000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.steps <= 1000000:
        parser.error("steps must be 2–1000000")
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    task, sampler = CounterTask(args.steps), Fixture()
    config = VotingConfig(max_calls=args.steps * 10)
    with Journal(args.output / "journal.sqlite3") as journal:
        paused = asyncio.run(
            Maker(task, sampler, config, journal).run("stress", pause_after=args.steps // 2)
        )
        assert paused["status"] == "paused"
    with Journal(args.output / "journal.sqlite3") as journal:
        report = asyncio.run(Maker(task, sampler, config, journal).run("stress"))
        journal.verify("stress")
        for expected, row in enumerate(
            journal.conn.execute(
                "SELECT decision FROM maker_steps WHERE run_id='stress' ORDER BY step"
            ),
            1,
        ):
            assert json.loads(row[0])["action"] == expected
        assert report["status"] == "completed" and report["steps"] == args.steps
    report.update(
        llm=False,
        interrupted_after=paused["steps"],
        integrity_verified=True,
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
