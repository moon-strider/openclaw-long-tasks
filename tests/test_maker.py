import asyncio
import json
from collections import Counter
from dataclasses import replace

import pytest

from long_tasks.hanoi import Hanoi, apply_move, evaluate_moves, oracle_moves
from long_tasks.maker import (
    Candidate,
    Journal,
    Maker,
    MakerFailure,
    Sample,
    VotingConfig,
    strict_json,
    winner,
)


class CountingTask:
    specification = {"task": "test_counter", "version": 1}
    initial_state = 0
    total_steps = 4

    def prompt(self, state):
        return str(state)

    def parse(self, text, state):
        value = strict_json(text)
        if type(value) is not int:
            raise ValueError("integer required")
        return Candidate(value, value)

    def completed(self, state):
        return state == self.total_steps


class Sampler:
    identity = {"implementation": "deterministic_test_fixture"}

    def __init__(self, function=None):
        self.calls = []
        self.function = function or (lambda prompt, index: Sample(str(int(prompt) + 1)))

    async def sample(self, prompt, index):
        self.calls.append((prompt, index))
        return self.function(prompt, index)


def test_margin_is_not_first_to_k_or_simple_plurality():
    assert winner(Counter(a=3, b=2), 3) is None
    assert winner(Counter(a=5, b=2), 3) == "a"
    assert winner(Counter(a=2), 3) is None
    assert winner(Counter(a=3), 3) == "a"
    assert winner(Counter(a=4, b=4), 1) is None


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', "NaN", "Infinity", "```json\n1\n```"])
def test_red_flags_do_not_repair_responses(text):
    with pytest.raises(ValueError):
        strict_json(text)


def test_hanoi_adapter_checks_legality_and_state_but_never_optimality():
    task = Hanoi(3)
    # A legal but suboptimal first move remains a valid candidate.
    wrong = task.parse('{"move":[1,0,1],"positions":[1,0,0]}', task.initial_state)
    assert wrong.action == [1, 0, 1]
    with pytest.raises(ValueError):
        task.parse('{"move":[1,0,2],"positions":[2,1,1]}', task.initial_state)
    with pytest.raises(ValueError):
        apply_move([[3, 2, 1], [], []], [2, 0, 2])
    assert not evaluate_moves(task, [wrong.action])["passed"]


@pytest.mark.parametrize("disks", range(1, 9))
def test_independent_oracle_and_adapter_agree_on_legal_solution(disks):
    task = Hanoi(disks)
    moves = list(oracle_moves(disks))
    state = task.initial_state
    for move in moves:
        candidate = task.parse(
            json.dumps(
                {
                    "move": move,
                    "positions": [
                        next(i for i, peg in enumerate(apply_move(state["pegs"], move)) if d in peg)
                        for d in range(1, disks + 1)
                    ],
                }
            ),
            state,
        )
        state = candidate.state
    assert task.completed(state)
    assert len(moves) == 2**disks - 1
    assert evaluate_moves(task, moves)["passed"]


def test_durable_resume_skips_committed_steps_and_completed_run(tmp_path):
    sampler = Sampler()
    path = tmp_path / "journal.sqlite"
    config = VotingConfig(k=3)
    with Journal(path) as journal:
        report = asyncio.run(
            Maker(CountingTask(), sampler, config, journal).run("run", pause_after=2)
        )
        assert (report["status"], report["steps"], report["calls"]) == ("paused", 2, 6)
    with Journal(path) as journal:
        report = asyncio.run(Maker(CountingTask(), sampler, config, journal).run("run"))
        assert (report["status"], report["steps"], report["calls"]) == ("completed", 4, 12)
        assert len(sampler.calls) == 12
        asyncio.run(Maker(CountingTask(), sampler, config, journal).run("run"))
        assert len(sampler.calls) == 12
        with pytest.raises(MakerFailure, match="differs"):
            asyncio.run(Maker(CountingTask(), sampler, replace(config, k=2), journal).run("run"))


def test_votes_survive_crash_before_checkpoint_without_resampling(tmp_path, monkeypatch):
    sampler = Sampler()
    with Journal(tmp_path / "journal") as journal:
        maker = Maker(CountingTask(), sampler, VotingConfig(), journal)
        original = journal.accept

        def crash(*args):
            raise KeyboardInterrupt()

        monkeypatch.setattr(journal, "accept", crash)
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(maker.run("run"))
        assert journal.report("run")["steps"] == 0
        assert len(sampler.calls) == 3
        monkeypatch.setattr(journal, "accept", original)
        asyncio.run(maker.run("run"))
        assert len(sampler.calls) == 12


def test_correlated_wrong_answers_can_win_and_are_not_hidden(tmp_path):
    sampler = Sampler(lambda p, i: Sample("999"))
    with Journal(tmp_path / "journal") as journal:
        with pytest.raises(MakerFailure, match="Step budget"):
            asyncio.run(Maker(CountingTask(), sampler, VotingConfig(), journal).run("run"))
        report = journal.report("run")
        assert report["state"] == 999 and report["steps"] == 4
        assert report["status"] == "blocked"


def test_redflags_and_call_budget_are_bounded(tmp_path):
    values = [Sample("oops"), Sample("1", "length"), Sample("x" * 100)]
    sampler = Sampler(lambda p, i: values[i % 3])
    with Journal(tmp_path / "journal") as journal:
        config = VotingConfig(max_calls=6, max_response_bytes=64, retain_responses=True)
        with pytest.raises(MakerFailure, match="budget"):
            asyncio.run(Maker(CountingTask(), sampler, config, journal).run("run"))
        report = journal.report("run")
        assert report["calls"] == 6 and report["steps"] == 0
        assert report["red_flags"] == {
            "invalid_candidate": 2,
            "nonterminal_or_truncated": 2,
            "response_too_long": 2,
        }


def test_cancellation_drains_owned_calls_and_charges_interrupted_reservations(tmp_path):
    async def scenario(journal):
        entered = asyncio.Event()
        active = set()

        class Hanging(Sampler):
            async def sample(self, p, i):
                active.add(i)
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    active.remove(i)

        config = VotingConfig(max_calls=15)
        maker = Maker(CountingTask(), Hanging(), config, journal)
        work = asyncio.create_task(maker.run("run"))
        await entered.wait()
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert not active
        assert journal.report("run")["calls"] == 3
        report = await Maker(CountingTask(), Sampler(), config, journal).run("run")
        assert report["calls"] == 15 and report["samples"]["abandoned"] == 3

    with Journal(tmp_path / "journal") as journal:
        asyncio.run(scenario(journal))


def test_single_writer_lock(tmp_path):
    with Journal(tmp_path / "journal"):
        with pytest.raises(MakerFailure, match="owns"):
            Journal(tmp_path / "journal")


def test_out_of_order_responses_do_not_bias_first_margin(tmp_path):
    async def scenario(journal):
        gate = asyncio.Event()

        class Delayed(Sampler):
            async def sample(self, p, i):
                if i % 3 == 0:
                    await gate.wait()
                    return Sample(str(int(p) + 1))
                gate.set()
                return Sample("999")

        report = await Maker(CountingTask(), Delayed(), VotingConfig(k=1), journal).run("run")
        assert report["status"] == "completed"
        assert report["calls"] == 12  # Includes already dispatched surplus samples.

    with Journal(tmp_path / "journal") as journal:
        asyncio.run(scenario(journal))


def test_journal_detects_corrupted_checkpoint_before_resuming(tmp_path):
    with Journal(tmp_path / "journal") as journal:
        maker = Maker(CountingTask(), Sampler(), VotingConfig(), journal)
        asyncio.run(maker.run("run", pause_after=2))
        journal.verify("run")
        with journal.conn:
            journal.conn.execute("UPDATE maker_runs SET state='999' WHERE id='run'")
        with pytest.raises(MakerFailure, match="checkpoint"):
            asyncio.run(maker.run("run"))
