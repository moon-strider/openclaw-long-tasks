import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from long_tasks import execution
from long_tasks.execution import execution_cancelled, run_process
from long_tasks.integrations import OpenClawCliAgentRunner, OpenClawCliNotificationTransport
from long_tasks.sampling import HTTPSampler, SamplingConfig


def test_process_stdin_and_output_are_bounded_and_utf8():
    assert (
        run_process(
            [sys.executable, "-c", "import sys; print(sys.stdin.read())"], prompt="привет"
        ).strip()
        == "привет"
    )
    with pytest.raises(ValueError, match="input limit"):
        run_process([sys.executable, "-c", "pass"], prompt="x" * 100, max_bytes=10)
    with pytest.raises(ValueError, match="byte limit"):
        run_process([sys.executable, "-c", "print('x' * 10000)"], max_bytes=100)
    with pytest.raises(RuntimeError, match="code 7"):
        run_process([sys.executable, "-c", "import sys; sys.stderr.write('private'); sys.exit(7)"])


def test_timeout_kills_descendants_before_delayed_side_effect(tmp_path):
    marker = tmp_path / "child-finished"
    child = f"import time; from pathlib import Path; time.sleep(.5); Path({str(marker)!r}).touch()"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(5)"
    with pytest.raises(TimeoutError):
        run_process([sys.executable, "-c", parent], timeout=0.15)
    time.sleep(0.6)
    assert not marker.exists()


def test_exited_group_permission_race_preserves_output_error(monkeypatch):
    children = []
    start = execution.subprocess.Popen
    signal_group = execution.os.killpg
    first_signal = True

    def tracked_start(*args, **kwargs):
        child = start(*args, **kwargs)
        children.append(child)
        return child

    def exited_group(pid, sig):
        nonlocal first_signal
        if first_signal:
            first_signal = False
            assert children[0].wait(timeout=5) == 0
            raise PermissionError("group leader has exited")
        return signal_group(pid, sig)

    monkeypatch.setattr(execution.subprocess, "Popen", tracked_start)
    monkeypatch.setattr(execution.os, "killpg", exited_group)
    with pytest.raises(ValueError, match="byte limit"):
        run_process([sys.executable, "-c", "print('x' * 10000)"], max_bytes=100)
    assert children[0].returncode == 0


def test_process_observes_execution_cancellation():
    cancelled = threading.Event()
    token = execution_cancelled.set(cancelled)
    cancelled.set()
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            run_process([sys.executable, "-c", "import time; time.sleep(5)"])
    finally:
        execution_cancelled.reset(token)


@pytest.fixture
def fake_binary(tmp_path):
    def create(envelope):
        path = tmp_path / "fake-openclaw"
        path.write_text(
            "#!"
            + sys.executable
            + "\nimport json,sys\nprompt=sys.stdin.read()\nassert '--message-file' in sys.argv\nprint("
            + repr(json.dumps(envelope))
            + ")\n"
        )
        path.chmod(0o755)
        return str(path)

    return create


def test_runner_uses_pinned_isolated_turn_and_creates_fresh_receipt(tmp_path, fake_binary):
    config = tmp_path / "config.json"
    config.write_text("{}")
    final = {"summary": "done", "artifacts": {}}
    binary = fake_binary({"ok": True, "status": "ok", "final": json.dumps(final)})
    runner = OpenClawCliAgentRunner(config_path=config, openclaw_bin=binary)
    task = SimpleNamespace(goal="do work", shared_state={})
    step = SimpleNamespace(instructions="do work", verification={})
    output = runner.run_step(task, step, 1, tmp_path / "task" / "attempt")
    assert json.loads(Path(output["artifacts"]["receipt"]).read_text()) == final
    with pytest.raises(ValueError, match="already exists"):
        runner.run_step(task, step, 1, tmp_path / "task" / "attempt")


@pytest.mark.parametrize(
    "envelope",
    [
        {"ok": False, "status": "error", "final": "{}"},
        {"ok": True, "status": "ok", "final": "```json\n{}\n```"},
        {"ok": True, "status": "ok", "final": '{"summary":""}'},
        {
            "ok": True,
            "status": "ok",
            "final": '{"summary":"done","artifacts":{"bad":"/etc/passwd"}}',
        },
        {"ok": True, "status": "ok", "final": '{"summary":"done","surprise":true}'},
    ],
)
def test_runner_rejects_unverified_or_malformed_results(tmp_path, fake_binary, envelope):
    config = tmp_path / "config.json"
    config.write_text("{}")
    runner = OpenClawCliAgentRunner(config, fake_binary(envelope))
    with pytest.raises((ValueError, RuntimeError)):
        runner.run_step(
            SimpleNamespace(goal="goal", shared_state={}),
            SimpleNamespace(instructions="task", verification={}),
            1,
            tmp_path / "task" / "attempt",
        )


def test_missing_openclaw_config_fails_before_launch(tmp_path):
    with pytest.raises(ValueError, match="pinned"):
        OpenClawCliAgentRunner().run_step(None, None, 1, tmp_path)


def test_sampler_forwards_seed_and_limits_without_leaking_auth(monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-secret")

    def respond(request):
        body = json.loads(request.content)
        assert body["seed"] == 14 and body["max_tokens"] == 256
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "1"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 3},
            },
        )

    async def run():
        sampler = HTTPSampler(
            SamplingConfig(api_key_env="TEST_MODEL_KEY"), transport=httpx.MockTransport(respond)
        )
        try:
            assert "test-secret" not in json.dumps(sampler.identity)
            sample = await sampler.sample("test", 3)
            assert sample.text == "1" and sample.usage == {"total_tokens": 3}
        finally:
            await sampler.close()

    asyncio.run(run())


def test_sampler_forwards_format_and_preserves_unstructured_identity():
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "move", "schema": {"type": "object"}},
    }
    config = SamplingConfig(response_format=response_format)
    response_format["type"] = "changed by caller"

    def respond(request):
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
        )

    async def run():
        sampler = HTTPSampler(config, transport=httpx.MockTransport(respond))
        legacy = HTTPSampler(SamplingConfig(), transport=httpx.MockTransport(respond))
        try:
            assert "response_format" not in legacy.identity
            assert sampler.identity != legacy.identity
            assert (await sampler.sample("one move", 0)).text == "{}"
        finally:
            await sampler.close()
            await legacy.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="private upstream diagnostics"),
        httpx.Response(200, text="x" * 300),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(
            200, json={"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}
        ),
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "1"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": -1},
            },
        ),
    ],
)
def test_sampler_rejects_bad_upstream_responses(response):
    async def run():
        sampler = HTTPSampler(
            SamplingConfig(max_response_bytes=256),
            transport=httpx.MockTransport(lambda r: response),
        )
        try:
            with pytest.raises((RuntimeError, ValueError)) as error:
                await sampler.sample("test", 0)
            assert "private upstream" not in str(error.value)
        finally:
            await sampler.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "config",
    [
        {"base_url": "http://remote.example/v1"},
        {"base_url": "https://secret:password@example.com"},
        {"temperature": float("nan")},
        {"seed": True},
        {"max_tokens": 0},
        {"model": ""},
    ],
)
def test_invalid_sampling_configuration(config):
    with pytest.raises(ValueError):
        SamplingConfig(**config)


def test_remote_sampler_requires_selected_credential(monkeypatch):
    monkeypatch.delenv("MAKER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MAKER_API_KEY"):
        HTTPSampler(SamplingConfig(base_url="https://example.com/v1"))


def test_notification_transport_reports_failure_without_throwing(tmp_path):
    transport = OpenClawCliNotificationTransport(openclaw_bin=str(tmp_path / "missing"))
    task = SimpleNamespace(notify_chat_id="test", reply_message_id=None)
    assert not transport.send(task, "message")
    assert transport.last_error() == "FileNotFoundError"


@pytest.mark.parametrize("status", ["waiting_user", "blocked"])
def test_runner_can_request_a_reply_or_record_a_blocker(tmp_path, fake_binary, status):
    from long_tasks.runtime import NeedsUserInput, TaskBlocked

    config = tmp_path / "config.json"
    config.write_text("{}")
    binary = fake_binary(
        {
            "ok": True,
            "status": "ok",
            "final": json.dumps({"status": status, "summary": "Need the brief"}),
        }
    )
    runner = OpenClawCliAgentRunner(config, binary)
    expected = NeedsUserInput if status == "waiting_user" else TaskBlocked
    with pytest.raises(expected, match="Need the brief"):
        runner.run_step(
            SimpleNamespace(goal="Review", shared_state={}),
            SimpleNamespace(instructions="Review", verification={}),
            1,
            tmp_path / "task" / "attempt",
        )


def test_delivery_messages_fit_the_configured_limit():
    from long_tasks.delivery import build_delivery_payload
    from long_tasks.models import TaskStatus

    for status in [TaskStatus.COMPLETED, TaskStatus.WAITING_USER]:
        task = SimpleNamespace(status=status, final_report_max_chars=256)
        message, attachments = build_delivery_payload(task, "x" * 10000)
        assert len(message) <= 256 and not attachments
