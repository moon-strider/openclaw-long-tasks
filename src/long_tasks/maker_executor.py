"""Run a bounded slice of a durable MAKER job as a scheduler step."""

import asyncio
import json
from pathlib import Path

from .execution import execution_cancelled
from .hanoi import Hanoi
from .maker import Journal, Maker, MakerFailure, VotingConfig, strict_json
from .runtime import StepDeferred, TaskBlocked
from .sampling import HTTPSampler, SamplingConfig


class MakerStepExecutor:
    def __init__(self, journal_path: Path):
        self.journal_path = journal_path

    def execute(self, task, step, attempt_count, artifacts_dir):
        spec = strict_json(step.instructions)
        if not isinstance(spec, dict) or set(spec) - {
            "disks",
            "sampling",
            "voting",
            "steps_per_pass",
            "state_mode",
            "prompt_mode",
        }:
            raise ValueError("Invalid MAKER step specification")
        chunk = spec.get("steps_per_pass", 100)
        if type(chunk) is not int or not 1 <= chunk <= 10000:
            raise ValueError("steps_per_pass must be 1–10000")
        cancel = execution_cancelled.get()

        async def run():
            sampler = HTTPSampler(SamplingConfig(**spec.get("sampling", {})))
            try:
                with Journal(self.journal_path) as journal:
                    maker = Maker(
                        Hanoi(
                            spec.get("disks", 3),
                            spec.get("state_mode", "model"),
                            spec.get("prompt_mode", "full"),
                        ),
                        sampler,
                        VotingConfig(**spec.get("voting", {})),
                        journal,
                    )
                    work = asyncio.create_task(
                        maker.run(task.id + ":" + step.id, pause_after=chunk)
                    )
                    try:
                        while not work.done():
                            if cancel is not None and cancel.is_set():
                                work.cancel()
                                raise RuntimeError("Task cancelled or execution lease lost")
                            await asyncio.wait({work}, timeout=0.1)
                        return await work
                    finally:
                        work.cancel()
                        await asyncio.gather(work, return_exceptions=True)
            finally:
                await sampler.close()

        try:
            report = asyncio.run(run())
        except MakerFailure as exc:
            raise TaskBlocked(str(exc)) from exc
        if report["status"] == "paused":
            raise StepDeferred(
                f"MAKER checkpoint: {report['steps']} steps, {report['calls']} calls"
            )
        result = artifacts_dir / "maker-report.json"
        result.write_text(json.dumps(report, indent=2) + "\n")
        return {
            "summary": f"MAKER reached the Hanoi goal in {report['steps']} steps using {report['calls']} calls",
            "artifacts": {"report": str(result.resolve())},
            "shared_state": {"maker_run_id": report["run_id"]},
        }
