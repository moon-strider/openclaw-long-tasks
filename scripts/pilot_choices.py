"""Calibrate move selection among all legal actions; never filter by optimality."""

import asyncio
import json
import time

import httpx
from pilot_hanoi import arguments, local_endpoint

from long_tasks.hanoi import apply_move, oracle_moves
from long_tasks.hanoi_choices import CHOICE_FORMAT, ChoiceHanoi


async def pilot(args, endpoint):
    task = ChoiceHanoi(args.disks, args.rule_style)
    states = []
    state = task.initial_state
    for move in oracle_moves(args.disks):
        states.append((state, move))
        state = {"pegs": apply_move(state["pegs"], move), "step": state["step"] + 1}
    states = states[: args.pilot_steps]
    protocol = {
        "kind": "oracle_state_choice_pilot",
        "closed_loop": False,
        "disks": args.disks,
        "seeds": [211, 223, 227],
        "temperature": 0.1,
        "rule_style": args.rule_style,
        "response_format": CHOICE_FORMAT,
        "prompts": [task.prompt(s) for s, _ in states],
    }
    (args.output / "protocol.json").write_text(json.dumps(protocol, indent=2))
    rows = []
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        for state, expected in states:
            prompt = task.prompt(state)
            for seed in protocol["seeds"]:
                body = {
                    "model": "local-single",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 32,
                    "temperature": 0.1,
                    "seed": seed,
                    "response_format": CHOICE_FORMAT,
                }
                started = time.monotonic()
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
                rows.append(
                    {
                        "state": state,
                        "seed": seed,
                        "prompt": prompt,
                        "response": value,
                        "correct": correct,
                        "error": error,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                )
                (args.output / "samples.json").write_text(json.dumps(rows, indent=2))
            print(
                json.dumps(
                    {
                        "state": state["step"],
                        "correct": sum(r["correct"] for r in rows),
                        "calls": len(rows),
                        "last": text,
                    }
                ),
                flush=True,
            )
    (args.output / "results.json").write_text(
        json.dumps({"correct": sum(r["correct"] for r in rows), "calls": len(rows)}, indent=2)
    )


if __name__ == "__main__":
    args = arguments()
    with local_endpoint(args) as endpoint:
        asyncio.run(pilot(args, endpoint))
