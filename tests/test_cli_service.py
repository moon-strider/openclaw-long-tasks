import json

import pytest
from typer.testing import CliRunner

from long_tasks.api import create_task
from long_tasks.cli import app
from long_tasks.config import Settings
from long_tasks.hanoi import Hanoi, oracle_moves
from long_tasks.integrations import OpenClawCliAgentRunner
from long_tasks.maker import Journal, Sample
from long_tasks.models import TaskStatus
from long_tasks.service import RuntimeService
from long_tasks.storage import TaskStore


class LocalTransport:
    def __init__(self):
        self.messages = []

    def send(self, task, message, attachments=None):
        self.messages.append((task, message, attachments))
        return True

    def last_error(self):
        return None


class FixtureSampler:
    identity = {"implementation": "hanoi_oracle_test_fixture_not_llm"}

    def __init__(self, *args):
        pass

    async def sample(self, prompt, index):
        return Sample(json.dumps({"move": list(oracle_moves(3))[index // 3]}))

    async def close(self):
        pass


def test_maker_scheduler_yields_and_resumes_same_journal(tmp_path, monkeypatch):
    monkeypatch.setattr("long_tasks.maker_executor.HTTPSampler", FixtureSampler)
    store = TaskStore(Settings(state_dir=tmp_path))
    task = create_task(
        store,
        title="Hanoi",
        goal="Move disks",
        steps=[
            {
                "title": "Microtasks",
                "kind": "maker",
                "max_attempts": 1,
                "instructions": json.dumps(
                    {
                        "disks": 3,
                        "state_mode": "deterministic",
                        "steps_per_pass": 2,
                        "voting": {"k": 3},
                    }
                ),
            }
        ],
    )
    transport = LocalTransport()
    scheduler = RuntimeService(store, OpenClawCliAgentRunner(), transport).build_scheduler()
    for expected in [TaskStatus.READY, TaskStatus.READY, TaskStatus.READY, TaskStatus.COMPLETED]:
        assert scheduler.tick("worker")[0].task_status == expected
    step = store.get_current_step(task.id)
    assert step.attempt_count == 1
    with Journal(store.settings.db_path) as journal:
        report = journal.report(task.id + ":" + step.id)
        assert report["steps"] == 7 and report["calls"] == 21
        journal.verify(report["run_id"])
    assert transport.messages[-1][0].status == TaskStatus.COMPLETED


def test_maker_budget_failure_blocks_macro_task(tmp_path, monkeypatch):
    class Broken(FixtureSampler):
        async def sample(self, p, i):
            return Sample("bad")

    monkeypatch.setattr("long_tasks.maker_executor.HTTPSampler", Broken)
    store = TaskStore(Settings(state_dir=tmp_path))
    task = create_task(
        store,
        title="Hanoi",
        goal="Move disks",
        steps=[
            {
                "title": "Microtasks",
                "kind": "maker",
                "instructions": json.dumps({"voting": {"max_calls": 3}}),
            }
        ],
    )
    result = (
        RuntimeService(store, OpenClawCliAgentRunner(), LocalTransport())
        .build_scheduler()
        .tick("worker")
    )
    assert result[0].task_status == TaskStatus.BLOCKED
    assert "budget" in store.get_task(task.id).last_error


def test_cli_create_inspect_cancel_and_state_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("LONG_TASKS_STATE_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "tasks",
            "create",
            "--title",
            "Example",
            "--goal",
            "Do work",
            "--steps-json",
            '[{"title":"First","instructions":"Read the brief"}]',
        ],
    )
    assert result.exit_code == 0, result.output
    task_id = json.loads(result.stdout)["id"]
    for args in [["tasks", "list"], ["tasks", "show", task_id], ["tasks", "cancel", task_id]]:
        result = runner.invoke(app, args)
        assert result.exit_code == 0 and task_id in result.stdout
    assert runner.invoke(app, ["tasks", "show", "missing"]).exit_code == 1
    assert (
        runner.invoke(app, ["--state-dir", str(tmp_path / "second"), "tasks", "list"]).exit_code
        == 0
    )


def test_cli_resume_and_local_tick(tmp_path, monkeypatch):
    monkeypatch.setenv("LONG_TASKS_STATE_DIR", str(tmp_path))
    store = TaskStore()
    task = create_task(
        store,
        title="Example",
        goal="Do work",
        steps=[{"title": "First", "instructions": "Do work"}],
    )
    with store.transaction() as conn:
        conn.execute("UPDATE tasks SET status='waiting_user' WHERE id=?", (task.id,))
    runner = CliRunner()
    assert (
        runner.invoke(
            app,
            ["tasks", "resume-latest-for-chat", "--chat-id", "local", "--reply-text", "continue"],
        ).exit_code
        == 0
    )
    with store.transaction() as conn:
        conn.execute("UPDATE tasks SET status='failed' WHERE id=?", (task.id,))
    assert runner.invoke(app, ["tasks", "resume", task.id]).exit_code == 0
    # Missing pinned config becomes a recorded execution retry, with local delivery only.
    result = runner.invoke(app, ["tasks", "tick"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"][0]["did_work"]


def test_maker_cli_runs_and_shows_persisted_result(tmp_path, monkeypatch):
    monkeypatch.setattr("long_tasks.maker_cli.HTTPSampler", FixtureSampler)
    monkeypatch.setenv("LONG_TASKS_STATE_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        app, ["maker", "hanoi", "--run-id", "example", "--state-mode", "deterministic"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "completed"
    assert runner.invoke(app, ["maker", "show", "example"]).exit_code == 0
    assert runner.invoke(app, ["maker", "show", "missing"]).exit_code != 0
    assert runner.invoke(app, ["maker", "hanoi", "--run-id", "example", "--k", "2"]).exit_code == 1


def test_service_dispatches_ordinary_runner_and_rejects_invalid_interval(tmp_path):
    class Runner:
        def run_step(self, *args):
            return {"summary": "done"}

    store = TaskStore(Settings(state_dir=tmp_path))
    create_task(
        store,
        title="Example",
        goal="Do work",
        steps=[{"title": "First", "instructions": "Do work"}],
    )
    service = RuntimeService(store, Runner(), LocalTransport())
    assert service.build_scheduler().tick("test")[0].task_status == TaskStatus.COMPLETED
    with pytest.raises(ValueError):
        service.run_forever(sleep_seconds=0)


def test_hanoi_phase_prompts_keep_oracle_out_of_validation():
    task = Hanoi(3, "deterministic", "phase")
    assert "Move disk 1" in task.prompt(task.initial_state)
    # The adapter accepts any legal action even when it differs from the desired phase.
    state = task.parse('{"move":[1,0,1]}', task.initial_state).state
    assert "between peg 0 and peg 2" in task.prompt(state)
    assert state["pegs"] == [[3, 2], [1], []]


def test_cli_enqueue_validates_and_persists_without_inference(tmp_path, monkeypatch):
    monkeypatch.setenv("LONG_TASKS_STATE_DIR", str(tmp_path))
    result = CliRunner().invoke(
        app,
        [
            "maker",
            "enqueue",
            "--state-mode",
            "deterministic",
            "--prompt-mode",
            "phase",
            "--steps-per-pass",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    task_id = json.loads(result.stdout)["id"]
    store = TaskStore()
    assert store.get_task(task_id).status == TaskStatus.READY
    spec = json.loads(store.get_current_step(task_id).instructions)
    assert spec["steps_per_pass"] == 2
    assert not store.list_attempts(task_id)
