"""Exploratory oracle-state calibration; this is not a closed-loop solve."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import httpx

from long_tasks.hanoi import Hanoi, apply_move, oracle_moves

MOVE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "hanoi_move",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "move": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 3,
                    "maxItems": 3,
                }
            },
            "required": ["move"],
            "additionalProperties": False,
        },
    },
}


def table_prompt(task, state, examples=False):
    if state["step"] % 2 == 0:
        current = next(i for i, peg in enumerate(state["pegs"]) if 1 in peg)
        table = "0: 2\n1: 0\n2: 1" if task.disks % 2 else "0: 1\n1: 2\n2: 0"
        text = (
            "Execute a single directed move of disk 1.\n"
            "The following lookup table maps each SOURCE peg to its DESTINATION peg:\n"
            + table
            + f"\nThe current SOURCE peg is {current}.\n"
            "Read the row whose key equals the SOURCE. Its value is the DESTINATION. "
            "Do not use a different row or reverse the mapping.\n"
        )
        if examples:
            text = (
                "Example using a DIFFERENT table: mapping {0:1,1:0,2:2}, SOURCE=1 "
                'gives {"move":[1,1,0]}.\nNow use the actual table:\n'
            ) + text
    else:
        available = [
            (i, peg[-1] if peg else None) for i, peg in enumerate(state["pegs"]) if 1 not in peg
        ]
        text = (
            "Choose one move between the following two pegs. Only the listed top disks matter.\n"
            + "\n".join(
                f"PEG {peg}: " + ("EMPTY" if disk is None else f"TOP DISK {disk}")
                for peg, disk in available
            )
            + "\nIf one peg is EMPTY, move the other peg's top disk to the empty peg. "
            "If neither is empty, compare the two disk numbers: move the SMALLER disk onto the LARGER disk. "
            "SOURCE means where the chosen disk is now. DESTINATION means the other peg.\n"
        )
        if examples:
            text = (
                'Example: PEG 0 has TOP DISK 8; PEG 2 has TOP DISK 5. Answer: {"move":[5,2,0]}.\n'
                'Example: PEG 1 is EMPTY; PEG 0 has TOP DISK 6. Answer: {"move":[6,0,1]}.\n'
                "Now answer the actual input:\n"
            ) + text
    return text + 'Return only {"move":[DISK,SOURCE,DESTINATION]} with integer values. /no_think'


@contextmanager
def local_endpoint(args):
    processes = []
    args.output.mkdir(parents=True, exist_ok=True)
    llama_args = [
        str(args.llama),
        "-m",
        str(args.model_file),
        "--host",
        "127.0.0.1",
        "--port",
        "18941",
        "--alias",
        "local-model",
        "-c",
        "8192",
        "-t",
        "4",
        "-tb",
        "4",
        "-np",
        "1",
        "-ngl",
        "0",
        "--jinja",
        "--chat-template-kwargs",
        '{"enable_thinking":false}',
        "--reasoning",
        "off",
    ]
    env = dict(os.environ, LLM_BASE_URL="http://127.0.0.1:18941/v1", LLM_MODEL="local-model")
    env["PYTHONPATH"] = str(args.swarm / "src")
    try:
        for command, filename, endpoint in [
            (llama_args, "llama.log", "http://127.0.0.1:18941/health"),
            (
                [str(args.swarm_python), "-m", "swarm_of_experts", "serve", "--port", "18942"],
                "swarm.log",
                "http://127.0.0.1:18942/health",
            ),
        ]:
            with (args.output / filename).open("w") as log:
                proc = subprocess.Popen(
                    command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
                )
            processes.append(proc)
            for _ in range(240):
                if proc.poll() is not None:
                    raise RuntimeError(f"Server exited: {filename}")
                try:
                    with urllib.request.urlopen(endpoint, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.5)
            else:
                raise TimeoutError(endpoint)
        (args.output / "launch.json").write_text(
            json.dumps(
                {
                    "llama_arguments": llama_args,
                    "model_file": args.model_file.name,
                    "swarm_commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=args.swarm, text=True
                    ).strip(),
                },
                indent=2,
            )
        )
        yield "http://127.0.0.1:18942/v1"
    finally:
        for proc in reversed(processes):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()


async def pilot(args, endpoint):
    task = Hanoi(3, "deterministic", "micro")
    states = []
    state = task.initial_state
    for move in oracle_moves(3):
        states.append((state, move))
        state = {"pegs": apply_move(state["pegs"], move), "step": state["step"] + 1}
    variants = [
        ("micro", False, False, 0.7),
        ("table", False, False, 0.1),
        ("table-json", True, False, 0.1),
        ("examples-json", True, True, 0.1),
    ]
    protocol = {
        "kind": "oracle_state_pilot",
        "closed_loop": False,
        "disks": 3,
        "seeds": [211, 223, 227],
        "variants": variants,
        "states": [s for s, _ in states],
        "response_format": MOVE_FORMAT,
        "purpose": "select a protocol before seven-disk closed-loop evaluation",
    }
    (args.output / "protocol.json").write_text(json.dumps(protocol, indent=2))
    rows = []
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        for name, constrained, examples, temperature in variants:
            for state, expected in states:
                prompt = (
                    task.prompt(state) if name == "micro" else table_prompt(task, state, examples)
                )
                for seed in protocol["seeds"]:
                    body = {
                        "model": "local-single",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 96,
                        "temperature": temperature,
                        "seed": seed,
                    }
                    if constrained:
                        body["response_format"] = MOVE_FORMAT
                    start = time.monotonic()
                    response = await client.post(endpoint + "/chat/completions", json=body)
                    response.raise_for_status()
                    value = response.json()
                    choice = value["choices"][0]
                    text = choice["message"]["content"]
                    error = None
                    try:
                        candidate = task.parse(text, state)
                        correct = choice["finish_reason"] == "stop" and candidate.action == expected
                    except ValueError as exc:
                        correct, error = False, str(exc)
                    row = {
                        "variant": name,
                        "state": state,
                        "seed": seed,
                        "prompt": prompt,
                        "response": value,
                        "correct": correct,
                        "error": error,
                        "elapsed_seconds": round(time.monotonic() - start, 3),
                    }
                    rows.append(row)
                    (args.output / "samples.json").write_text(json.dumps(rows, indent=2))
                subset = [r for r in rows if r["variant"] == name]
                print(
                    json.dumps(
                        {
                            "variant": name,
                            "state": state["step"],
                            "correct": sum(r["correct"] for r in subset),
                            "calls": len(subset),
                            "last": text,
                        }
                    ),
                    flush=True,
                )
    report = {
        name: {
            "correct": sum(r["correct"] for r in rows if r["variant"] == name),
            "calls": sum(r["variant"] == name for r in rows),
        }
        for name, *_ in variants
    }
    (args.output / "results.json").write_text(json.dumps(report, indent=2))


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llama", type=Path, required=True)
    parser.add_argument("--model-file", type=Path, required=True)
    parser.add_argument("--swarm", type=Path, required=True)
    parser.add_argument("--swarm-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disks", type=int, default=3)
    parser.add_argument("--pilot-steps", type=int, default=7)
    parser.add_argument("--rule-style", choices=["negative", "positive"], default="negative")
    return parser.parse_args()


if __name__ == "__main__":
    args = arguments()
    with local_endpoint(args) as endpoint:
        asyncio.run(pilot(args, endpoint))
