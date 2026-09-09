"""Exercise an installed OpenClaw CLI against a local or hosted model API.

No channels are configured and no messages are sent to other people.
The random read sentinel is not included in the prompt.
"""

import argparse
import json
import tempfile
import uuid
from pathlib import Path

from long_tasks.execution import run_process


def check(args):
    with tempfile.TemporaryDirectory(prefix="openclaw-check-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        expected = "read-" + uuid.uuid4().hex
        (workspace / "sentinel.txt").write_text(expected + "\n")
        model = {
            "id": args.model,
            "name": "Local integration check",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 16384,
            "maxTokens": 512,
        }
        config = {
            "agents": {
                "defaults": {"model": {"primary": "test/" + args.model}, "skipBootstrap": True}
            },
            "models": {
                "mode": "merge",
                "providers": {
                    "test": {
                        "baseUrl": args.base_url,
                        "apiKey": "local",
                        "api": "openai-completions",
                        "models": [model],
                    }
                },
            },
            "tools": {"allow": ["read"], "fs": {"workspaceOnly": True}}
            if args.mode == "read"
            else {"deny": ["*"]},
            "plugins": {"enabled": False},
        }
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config))
        prompt = "Use the read tool to read sentinel.txt. Then reply with ONLY its exact contents. /no_think"
        if args.mode == "text":
            expected = "OPENCLAW_OK"
            prompt = "Reply exactly OPENCLAW_OK and nothing else. /no_think"
        command = [
            args.openclaw_bin,
            "agent",
            "exec",
            "--config",
            str(config_path),
            "--cwd",
            str(workspace),
            "--message-file",
            "-",
            "--json",
            "--thinking",
            "off",
            "--code-mode",
            "direct",
            "--local-model-lean",
            "--timeout",
            str(args.timeout),
        ]
        record = {"mode": args.mode, "model": args.model, "expected": expected, "config": config}
        try:
            raw = run_process(command, prompt=prompt, cwd=workspace, timeout=args.timeout + 10)
            payload = json.loads(raw)
            record["envelope"] = payload
            record["passed"] = (
                payload.get("ok") is True and payload.get("final", "").strip() == expected
            )
        except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
            record.update(passed=False, error=str(exc))
        print(json.dumps(record, indent=2))
        return record["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openclaw-bin", default="openclaw")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="local-single")
    parser.add_argument("--mode", choices=["text", "read"], default="read")
    parser.add_argument("--timeout", type=int, default=300)
    raise SystemExit(0 if check(parser.parse_args()) else 1)
