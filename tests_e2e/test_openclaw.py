"""Actual OpenClaw process and SQLite runtime; deterministic model fixture."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from long_tasks.api import create_task
from long_tasks.config import Settings
from long_tasks.integrations import OpenClawCliAgentRunner
from long_tasks.models import TaskStatus
from long_tasks.runtime import InMemoryNotificationSink, Scheduler, Worker
from long_tasks.storage import TaskStore


@pytest.mark.skipif(
    not os.environ.get("OPENCLAW_BIN"), reason="set OPENCLAW_BIN for installed CLI integration"
)
def test_installed_openclaw_commits_verified_macro_step(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            final = json.dumps({"summary": "fixture step complete", "artifacts": {}})
            base = {
                "id": "fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture",
            }
            chunks = [
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": final},
                            "finish_reason": None,
                        }
                    ],
                },
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            data = (
                "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        config = {
            "agents": {"defaults": {"model": {"primary": "fixture/local"}, "skipBootstrap": True}},
            "models": {
                "providers": {
                    "fixture": {
                        "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                        "apiKey": "local",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "local",
                                "name": "Fixture",
                                "reasoning": False,
                                "input": ["text"],
                                "contextWindow": 16384,
                                "maxTokens": 512,
                            }
                        ],
                    }
                }
            },
            "tools": {"deny": ["*"]},
            "plugins": {"enabled": False},
        }
        config_path = tmp_path / "openclaw.json"
        config_path.write_text(json.dumps(config))
        runner = OpenClawCliAgentRunner(config_path, os.environ["OPENCLAW_BIN"], timeout_seconds=60)

        class Executor:
            def execute(self, *args):
                return runner.run_step(*args)

        store = TaskStore(Settings(state_dir=tmp_path / "state"))
        task = create_task(
            store,
            title="CLI check",
            goal="Return a structured result",
            steps=[
                {
                    "title": "Report",
                    "instructions": "Return a summary",
                    "verification": {"artifact_keys": ["receipt"]},
                }
            ],
        )
        notifications = InMemoryNotificationSink()
        result = Scheduler(store, Worker(store, Executor(), notifications)).tick("test")
        assert result[0].task_status == TaskStatus.COMPLETED
        receipt = Path(store.get_current_step(task.id).artifact_paths[0])
        assert json.loads(receipt.read_text())["summary"] == "fixture step complete"
        assert len(requests) == 1 and len(notifications.messages) == 1
        assert store.list_attempts(task.id)[0]["status"] == "success"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
