"""Direct MAKER commands; the same engine also runs inside the scheduler."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import typer

from .api import create_task
from .config import Settings
from .hanoi import Hanoi
from .maker import Journal, Maker, MakerFailure, VotingConfig
from .sampling import HTTPSampler, SamplingConfig
from .storage import TaskStore

app = typer.Typer(no_args_is_help=True)


@app.command("enqueue")
def enqueue(
    title: str = "Hanoi microtasks",
    disks: int = 3,
    base_url: str = "http://127.0.0.1:8000/v1",
    model: str = "local-single",
    state_mode: str = "model",
    prompt_mode: str = "full",
    k: int = 3,
    max_calls: int = 1000,
    max_samples: int = 24,
    steps_per_pass: int = 100,
    temperature: float = 0.7,
    seed: int = 11,
):
    """Queue a durable Hanoi job for tasks tick or service run."""
    try:
        Hanoi(disks, state_mode, prompt_mode)
        sampling = SamplingConfig(
            base_url=base_url, model=model, temperature=temperature, seed=seed
        )
        voting = VotingConfig(k=k, max_calls=max_calls, max_samples=max_samples)
        if not 1 <= steps_per_pass <= 10000:
            raise ValueError("steps_per_pass must be 1–10000")
        task = create_task(
            TaskStore(),
            title=title,
            goal=f"Move {disks} Hanoi disks to peg 2",
            steps=[
                {
                    "title": "Solve Hanoi",
                    "kind": "maker",
                    "max_attempts": 1,
                    "instructions": json.dumps(
                        {
                            "disks": disks,
                            "state_mode": state_mode,
                            "prompt_mode": prompt_mode,
                            "sampling": asdict(sampling),
                            "voting": asdict(voting),
                            "steps_per_pass": steps_per_pass,
                        }
                    ),
                    "verification": {"artifact_keys": ["report"]},
                }
            ],
        )
        print(json.dumps({"id": task.id, "status": task.status.value}))
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("hanoi")
def hanoi(
    run_id: str = typer.Option(...),
    disks: int = 3,
    state_mode: str = "model",
    prompt_mode: str = "full",
    base_url: str = "http://127.0.0.1:8000/v1",
    model: str = "local-single",
    k: int = 3,
    concurrency: int = 3,
    max_samples: int = 24,
    max_calls: int = 1000,
    temperature: float = 0.7,
    seed: int = 11,
    pause_after: int | None = None,
    retain_responses: bool = False,
    journal: Path | None = None,
):
    """Start or resume a run with the same run id and configuration."""

    async def execute():
        sampler = HTTPSampler(
            SamplingConfig(base_url=base_url, model=model, temperature=temperature, seed=seed)
        )
        try:
            with Journal(journal or Settings().db_path) as log:
                engine = Maker(
                    Hanoi(disks, state_mode, prompt_mode),
                    sampler,
                    VotingConfig(
                        k=k,
                        concurrency=concurrency,
                        max_samples=max_samples,
                        max_calls=max_calls,
                        retain_responses=retain_responses,
                    ),
                    log,
                )
                try:
                    report = await engine.run(run_id, pause_after=pause_after)
                except MakerFailure:
                    if log.row(run_id):
                        print(json.dumps(log.report(run_id), indent=2))
                    raise
                print(json.dumps(report, indent=2))
        finally:
            await sampler.close()

    try:
        asyncio.run(execute())
    except (ValueError, OSError, MakerFailure) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        raise typer.Exit(130) from None


@app.command("show")
def show(run_id: str, journal: Path | None = None):
    path = journal or Settings().db_path
    if not path.is_file():
        raise typer.BadParameter("Journal does not exist")
    with Journal(path) as log:
        try:
            print(json.dumps(log.report(run_id), indent=2))
        except KeyError:
            raise typer.BadParameter("Unknown run id") from None
