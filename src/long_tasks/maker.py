"""Bounded first-to-ahead-by-k voting with durable, pure state transitions.

An independent implementation inspired by arXiv:2511.09030, not the authors' code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-finite JSON constant")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Sample:
    text: str
    finish_reason: str = "stop"
    usage: dict[str, int] | None = None


@dataclass(frozen=True)
class Candidate:
    action: Any
    state: Any

    @property
    def key(self) -> str:
        return canonical(asdict(self))


class MicroTask(Protocol):
    @property
    def specification(self) -> dict: ...
    @property
    def initial_state(self) -> Any: ...
    @property
    def total_steps(self) -> int: ...
    def prompt(self, state: Any) -> str: ...
    def parse(self, text: str, state: Any) -> Candidate: ...
    def completed(self, state: Any) -> bool: ...


class Sampler(Protocol):
    @property
    def identity(self) -> dict: ...
    async def sample(self, prompt: str, index: int) -> Sample: ...


@dataclass(frozen=True)
class VotingConfig:
    k: int = 3
    concurrency: int = 3
    max_samples: int = 24
    max_calls: int = 10000
    max_response_bytes: int = 8192
    step_timeout: float = 300
    retain_responses: bool = False

    def __post_init__(self):
        if type(self.retain_responses) is not bool:
            raise ValueError("retain_responses must be boolean")
        for name, low, high in [
            ("k", 1, 100),
            ("concurrency", 1, 32),
            ("max_samples", 1, 1000),
            ("max_calls", 1, 100_000_000),
            ("max_response_bytes", 64, 1_048_576),
        ]:
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} outside supported range")
        if self.k > self.max_samples:
            raise ValueError("k exceeds the per-step sample budget")
        if not 0 < self.step_timeout <= 3600:
            raise ValueError("step_timeout must be in (0, 3600]")


class MakerFailure(RuntimeError):
    pass


class Journal:
    """One local writer; accepted steps and their checkpoints commit together."""

    def __init__(self, path: Path):
        import fcntl

        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_suffix(self.path.suffix + ".maker.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise MakerFailure("Another MAKER process owns this journal") from None
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS maker_runs (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, specification TEXT NOT NULL,
                state TEXT NOT NULL, steps INTEGER NOT NULL DEFAULT 0,
                calls INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, error TEXT,
                created REAL NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS maker_samples (
                run_id TEXT NOT NULL REFERENCES maker_runs(id), step INTEGER NOT NULL,
                number INTEGER NOT NULL, status TEXT NOT NULL, candidate TEXT,
                reason TEXT, response TEXT, response_sha TEXT, usage TEXT,
                PRIMARY KEY(run_id,number)
            );
            CREATE INDEX IF NOT EXISTS maker_sample_step ON maker_samples(run_id,step);
            CREATE TABLE IF NOT EXISTS maker_steps (
                run_id TEXT NOT NULL REFERENCES maker_runs(id), step INTEGER NOT NULL,
                decision TEXT NOT NULL, votes TEXT NOT NULL, prior_hash TEXT NOT NULL,
                chain_hash TEXT NOT NULL, PRIMARY KEY(run_id,step)
            );
        """)

    def close(self):
        self.conn.close()
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def initialize(self, run_id: str, task: MicroTask, sampler: Sampler, config: VotingConfig):
        if not run_id or len(run_id) > 128:
            raise ValueError("run_id must have 1–128 characters")
        specification = {
            "protocol": 1,
            "task": task.specification,
            "sampler": sampler.identity,
            "voting": asdict(config),
        }
        fingerprint = digest(specification)
        with self.conn:
            row = self.conn.execute(
                "SELECT fingerprint FROM maker_runs WHERE id=?", (run_id,)
            ).fetchone()
            if row and row["fingerprint"] != fingerprint:
                raise MakerFailure("Resume configuration differs from the recorded experiment")
            now = time.time()
            self.conn.execute(
                "INSERT OR IGNORE INTO maker_runs(id,fingerprint,specification,state,status,created,updated) VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    fingerprint,
                    canonical(specification),
                    canonical(task.initial_state),
                    "paused",
                    now,
                    now,
                ),
            )
            # Reserved calls survive a crash; unknown responses never acquire a vote.
            self.conn.execute(
                "UPDATE maker_samples SET status='abandoned',reason='interrupted' WHERE run_id=? AND status='pending'",
                (run_id,),
            )

    def verify(self, run_id: str) -> None:
        row = self.row(run_id)
        if row is None:
            raise KeyError(run_id)
        specification = json.loads(row["specification"])
        if digest(specification) != row["fingerprint"]:
            raise MakerFailure("Journal specification digest mismatch")
        prior = row["fingerprint"]
        state, count = None, 0
        for step in self.conn.execute(
            "SELECT * FROM maker_steps WHERE run_id=? ORDER BY step", (run_id,)
        ):
            chain = digest(
                {
                    "prior": prior,
                    "step": count,
                    "decision": step["decision"],
                    "votes": json.loads(step["votes"]),
                }
            )
            if step["step"] != count or step["prior_hash"] != prior or step["chain_hash"] != chain:
                raise MakerFailure("Journal step chain mismatch")
            votes = Counter(json.loads(step["votes"]))
            if winner(votes, specification["voting"]["k"]) != step["decision"]:
                raise MakerFailure("Checkpoint lacks the recorded voting margin")
            prior = chain
            state = json.loads(step["decision"])["state"]
            count += 1
        if count != row["steps"] or (count and canonical(state) != row["state"]):
            raise MakerFailure("Journal checkpoint differs from accepted steps")
        calls = self.conn.execute(
            "SELECT count(*) FROM maker_samples WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        if calls != row["calls"]:
            raise MakerFailure("Journal call budget differs from reservations")

    def row(self, run_id: str):
        return self.conn.execute("SELECT * FROM maker_runs WHERE id=?", (run_id,)).fetchone()

    def samples(self, run_id: str, step: int):
        return self.conn.execute(
            "SELECT * FROM maker_samples WHERE run_id=? AND step=? ORDER BY number", (run_id, step)
        ).fetchall()

    def reserve(self, run_id: str, step: int, count: int, config: VotingConfig) -> list[int]:
        with self.conn:
            row = self.row(run_id)
            if row["steps"] != step:
                raise MakerFailure("Checkpoint changed while reserving votes")
            used = self.conn.execute(
                "SELECT count(*) FROM maker_samples WHERE run_id=? AND step=?", (run_id, step)
            ).fetchone()[0]
            count = min(count, config.max_samples - used, config.max_calls - row["calls"])
            if count <= 0:
                raise MakerFailure("Voting budget exhausted before reaching the required margin")
            numbers = list(range(row["calls"], row["calls"] + count))
            self.conn.executemany(
                "INSERT INTO maker_samples(run_id,step,number,status) VALUES (?,?,?,'pending')",
                [(run_id, step, n) for n in numbers],
            )
            self.conn.execute(
                "UPDATE maker_runs SET calls=calls+?,updated=? WHERE id=?",
                (count, time.time(), run_id),
            )
        return numbers

    def record(
        self,
        run_id: str,
        number: int,
        sample: Sample | None,
        candidate: Candidate | None,
        reason: str | None,
        retain: bool,
    ):
        with self.conn:
            result = self.conn.execute(
                "UPDATE maker_samples SET status=?,candidate=?,reason=?,response=?,response_sha=?,usage=? WHERE run_id=? AND number=? AND status='pending'",
                (
                    "valid" if candidate else "rejected",
                    candidate.key if candidate else None,
                    reason,
                    sample.text if sample and retain else None,
                    hashlib.sha256(sample.text.encode()).hexdigest() if sample else None,
                    canonical(sample.usage) if sample and sample.usage else None,
                    run_id,
                    number,
                ),
            )
            if result.rowcount != 1:
                raise MakerFailure("Vote already recorded or not reserved")

    def accept(self, run_id: str, step: int, candidate: Candidate, votes: Counter, done: bool):
        with self.conn:
            row = self.row(run_id)
            if row["steps"] != step:
                raise MakerFailure("Duplicate or stale step commit")
            previous = self.conn.execute(
                "SELECT chain_hash FROM maker_steps WHERE run_id=? AND step=?", (run_id, step - 1)
            ).fetchone()
            prior = previous[0] if previous else row["fingerprint"]
            chain = digest(
                {"prior": prior, "step": step, "decision": candidate.key, "votes": dict(votes)}
            )
            self.conn.execute(
                "INSERT INTO maker_steps VALUES (?,?,?,?,?,?)",
                (run_id, step, candidate.key, canonical(dict(votes)), prior, chain),
            )
            self.conn.execute(
                "UPDATE maker_runs SET state=?,steps=steps+1,status=?,error=NULL,updated=? WHERE id=?",
                (
                    canonical(candidate.state),
                    "completed" if done else "running",
                    time.time(),
                    run_id,
                ),
            )

    def status(self, run_id: str, status: str, error: str | None = None):
        with self.conn:
            self.conn.execute(
                "UPDATE maker_runs SET status=?,error=?,updated=? WHERE id=?",
                (status, error, time.time(), run_id),
            )

    def report(self, run_id: str) -> dict:
        row = self.row(run_id)
        if row is None:
            raise KeyError(run_id)
        counts = dict(
            self.conn.execute(
                "SELECT status,count(*) FROM maker_samples WHERE run_id=? GROUP BY status",
                (run_id,),
            ).fetchall()
        )
        reasons = dict(
            self.conn.execute(
                "SELECT reason,count(*) FROM maker_samples WHERE run_id=? AND reason IS NOT NULL GROUP BY reason",
                (run_id,),
            ).fetchall()
        )
        return {
            "run_id": run_id,
            "status": row["status"],
            "steps": row["steps"],
            "calls": row["calls"],
            "state": json.loads(row["state"]),
            "error": row["error"],
            "samples": counts,
            "red_flags": reasons,
            "specification": json.loads(row["specification"]),
        }


def winner(votes: Counter[str], k: int) -> str | None:
    ordered = votes.most_common(2)
    if ordered and ordered[0][1] - (ordered[1][1] if len(ordered) > 1 else 0) >= k:
        return ordered[0][0]
    return None


class Maker:
    def __init__(self, task: MicroTask, sampler: Sampler, config: VotingConfig, journal: Journal):
        self.task, self.sampler, self.config, self.journal = task, sampler, config, journal

    async def _vote(self, run_id: str, state: Any, step: int) -> tuple[Candidate, Counter]:
        votes: Counter[str] = Counter()
        # Replay in reservation order, not response-arrival order.
        for row in self.journal.samples(run_id, step):
            if row["candidate"]:
                votes[row["candidate"]] += 1
                if key := winner(votes, self.config.k):
                    return Candidate(**json.loads(key)), votes
        while True:
            numbers = self.journal.reserve(run_id, step, self.config.concurrency, self.config)
            calls = [
                asyncio.create_task(self.sampler.sample(self.task.prompt(state), n))
                for n in numbers
            ]
            try:
                results = await asyncio.gather(*calls, return_exceptions=True)
            finally:
                for call in calls:
                    call.cancel()
                await asyncio.gather(*calls, return_exceptions=True)
            selected = None
            selected_votes = None
            for number, result in zip(numbers, results, strict=True):
                candidate, reason, sample = None, None, None
                if isinstance(result, BaseException):
                    reason = "sampling_error"
                else:
                    sample = result
                    if result.finish_reason != "stop":
                        reason = "nonterminal_or_truncated"
                    elif len(result.text.encode()) > self.config.max_response_bytes:
                        reason = "response_too_long"
                        sample = Sample(
                            result.text.encode()[: self.config.max_response_bytes].decode(
                                "utf-8", errors="ignore"
                            ),
                            result.finish_reason,
                            result.usage,
                        )
                    else:
                        try:
                            candidate = self.task.parse(result.text, state)
                        except (ValueError, TypeError, KeyError, RecursionError):
                            reason = "invalid_candidate"
                self.journal.record(
                    run_id, number, sample, candidate, reason, self.config.retain_responses
                )
                if candidate and selected is None:
                    votes[candidate.key] += 1
                    if key := winner(votes, self.config.k):
                        selected = Candidate(**json.loads(key))
                        selected_votes = votes.copy()
            if selected is not None:
                assert selected_votes is not None
                return selected, selected_votes

    async def run(self, run_id: str, *, pause_after: int | None = None) -> dict:
        if pause_after is not None and (type(pause_after) is not int or pause_after < 1):
            raise ValueError("pause_after must be a positive integer")
        self.journal.initialize(run_id, self.task, self.sampler, self.config)
        self.journal.verify(run_id)
        before = self.journal.row(run_id)["steps"]
        if self.journal.row(run_id)["status"] == "completed":
            return self.journal.report(run_id)
        self.journal.status(run_id, "running")
        try:
            while (row := self.journal.row(run_id))["steps"] < self.task.total_steps:
                state, step = json.loads(row["state"]), row["steps"]
                async with asyncio.timeout(self.config.step_timeout):
                    candidate, votes = await self._vote(run_id, state, step)
                done = self.task.completed(candidate.state)
                self.journal.accept(run_id, step, candidate, votes, done)
                if done:
                    break
                if pause_after is not None and step + 1 - before >= pause_after:
                    self.journal.status(run_id, "paused")
                    return self.journal.report(run_id)
            if self.journal.row(run_id)["status"] != "completed":
                raise MakerFailure("Step budget exhausted before reaching the task goal")
        except asyncio.CancelledError:
            self.journal.status(run_id, "paused", "cancelled")
            raise
        except (MakerFailure, TimeoutError) as exc:
            self.journal.status(run_id, "blocked", str(exc) or "step deadline exceeded")
            raise MakerFailure(str(exc) or "Step deadline exceeded") from exc
        except BaseException:
            self.journal.status(run_id, "paused", "interrupted")
            raise
        return self.journal.report(run_id)
