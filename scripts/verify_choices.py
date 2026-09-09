"""Replay archived real-model choices, votes and state transitions without inference."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from long_tasks.hanoi import Hanoi, apply_move, evaluate_moves, oracle_moves
from long_tasks.hanoi_choices import ChoiceHanoi
from long_tasks.maker import canonical, digest, winner


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_experiment(folder):
    protocol = json.loads((folder / "protocol.json").read_text())
    results = json.loads((folder / "results.json").read_text())
    journal = json.loads((folder / "journal.json").read_text())
    require(results["protocol_sha256"] == digest(protocol), "Protocol digest mismatch")
    declared = {
        f"choices-d{protocol['task']['disks']}-k{k}-s{seed}"
        for seed in protocol["seeds"]
        for k in protocol["margins"]
    }
    rows = results["runs"]
    require(len(rows) == len(declared), "Incomplete or duplicate results")
    require({r["run_id"] for r in rows} == declared, "Undeclared case")
    require({r["id"] for r in journal["runs"]} == declared, "Journal run mismatch")
    require(len(journal["runs"]) == len(declared), "Duplicate journal run")
    total_calls = 0
    for result in rows:
        run_id = result["run_id"]
        run = next(r for r in journal["runs"] if r["id"] == run_id)
        spec = json.loads(run["specification"])
        task = ChoiceHanoi(
            spec["task"]["disks"],
            spec["task"]["rule_style"],
            spec["task"].get("routing_style", "mapping"),
        )
        require(spec["task"] == protocol["task"] == task.specification, "Task mismatch")
        require(spec["sampler"]["seed"] == result["seed"], "Seed mismatch")
        require(spec["voting"]["k"] == result["k"], "Voting margin mismatch")
        require(digest(spec) == run["fingerprint"], "Specification digest mismatch")
        calls = sorted(
            (s for s in journal["samples"] if s["run_id"] == run_id),
            key=lambda s: s["number"],
        )
        require([s["number"] for s in calls] == list(range(run["calls"])), "Call gap")
        traces = [
            json.loads(line) for line in (folder / f"{run_id}-calls.jsonl").read_text().splitlines()
        ]
        require(len(traces) == len(calls), "Trace count mismatch")
        by_number = {t["number"]: t for t in traces}
        require(len(by_number) == len(traces), "Duplicate trace")
        steps = sorted(
            (s for s in journal["steps"] if s["run_id"] == run_id), key=lambda s: s["step"]
        )
        state, prior, moves = task.initial_state, run["fingerprint"], []
        require([s["step"] for s in steps] == list(range(run["steps"])), "Step gap")
        # Include the final unaccepted step, if sampling ended before a quorum.
        for number in range(len(steps) + 1):
            votes, selected, selected_votes = Counter(), None, None
            for sample in (s for s in calls if s["step"] == number):
                trace = by_number[sample["number"]]
                require(trace["prompt"] == task.prompt(state), "Prompt differs from actual state")
                raw = trace.get("sample")
                if raw is None:
                    require(sample["status"] != "valid" and "error" in trace, "Missing response")
                    continue
                require(raw["text"] == sample["response"], "Response differs from trace")
                require(
                    hashlib.sha256(raw["text"].encode()).hexdigest() == sample["response_sha"],
                    "Response digest mismatch",
                )
                require(json.loads(sample["usage"]) == raw["usage"], "Usage mismatch")
                try:
                    candidate = task.parse(raw["text"], state)
                except ValueError:
                    candidate = None
                valid = raw["finish_reason"] == "stop" and candidate is not None
                require(valid == (sample["status"] == "valid"), "Sample validity mismatch")
                if valid:
                    require(candidate.key == sample["candidate"], "Candidate mismatch")
                    if selected is None:
                        votes[candidate.key] += 1
                        selected = winner(votes, result["k"])
                        if selected:
                            selected_votes = dict(votes)
            if number == len(steps):
                require(selected is None, "Unrecorded accepted step")
                break
            step = steps[number]
            require(selected == step["decision"], "Decision does not follow raw model votes")
            require(selected_votes == json.loads(step["votes"]), "Vote count mismatch")
            chain = digest(
                {"prior": prior, "step": number, "decision": selected, "votes": selected_votes}
            )
            require(step["prior_hash"] == prior and step["chain_hash"] == chain, "Chain mismatch")
            prior = chain
            decision = json.loads(selected)
            state = decision["state"]
            moves.append(decision["action"])
        require(all(0 <= s["step"] <= len(steps) for s in calls), "Unexpected sample step")
        require(canonical(state) == run["state"], "Final state mismatch")
        require(result["moves"] == moves, "Reported moves mismatch")
        require(result["report"]["calls"] == len(calls), "Reported call count mismatch")
        evaluation = evaluate_moves(Hanoi(task.task.disks), moves)
        require(result["evaluation"] == evaluation, "Independent evaluation mismatch")
        require(
            result["passed"] == (evaluation["passed"] and not result["error"]), "Success mismatch"
        )
        total_calls += len(calls)
    return {"cases": len(rows), "successes": sum(r["passed"] for r in rows), "calls": total_calls}


def verify_archive(root):
    index = json.loads((root / "index.json").read_text())
    for name, expected in index["files_sha256"].items():
        require(hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, f"Hash: {name}")
    reports = {entry: verify_experiment(root / entry) for entry in index["experiments"]}
    pilots = {entry: verify_pilot(root / entry) for entry in index["pilots"]}
    return {
        "experiments": reports,
        "pilot_calls": sum(p["calls"] for p in pilots.values()),
        "verified_files": len(index["files_sha256"]),
        "new_llm_calls": 0,
    }


def verify_pilot(folder):
    rows = json.loads((folder / "samples.json").read_text())
    results = json.loads((folder / "results.json").read_text())
    totals = {}
    for row in rows:
        response = row["response"]["choices"][0]
        text = response["message"]["content"]
        correct = False
        if "state" in row:
            state = row["state"]
            disks = sum(len(peg) for peg in state["pegs"])
            task = ChoiceHanoi(disks)
            reference = task.initial_state
            for step, expected in enumerate(oracle_moves(disks)):
                if step == state["step"]:
                    break
                reference = {
                    "pegs": apply_move(reference["pegs"], expected),
                    "step": step + 1,
                }
            require(state == reference, "Pilot state is not the declared oracle state")
            try:
                value = json.loads(text)
                if "variant" in row:
                    action = Hanoi(disks, "deterministic", "micro").parse(text, state).action
                else:
                    action = task.parse(json.dumps({"choice": value["choice"]}), state).action
                correct = action == expected
            except (ValueError, TypeError, KeyError):
                pass
        else:
            try:
                correct = json.loads(text)["destination"] == {0: 2, 1: 0, 2: 1}[row["source"]]
            except (ValueError, TypeError, KeyError):
                pass
        correct = correct and response["finish_reason"] == "stop"
        require(correct == row["correct"], "Pilot accuracy mismatch")
        group = row.get("style", row.get("variant", "all"))
        total = totals.setdefault(group, {"correct": 0, "calls": 0})
        total["calls"] += 1
        total["correct"] += correct
    expected_results = totals["all"] if set(totals) == {"all"} else totals
    require(results == expected_results, "Pilot summary mismatch")
    return {"calls": len(rows), "groups": totals}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    result = (
        verify_archive(args.path)
        if (args.path / "index.json").exists()
        else verify_experiment(args.path)
    )
    print(json.dumps(result, indent=2))
