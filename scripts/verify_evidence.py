"""Check archived evidence hashes, protocols and Hanoi evaluations without inference."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from long_tasks.hanoi import Hanoi, evaluate_moves
from long_tasks.maker import digest


def verify(root: Path) -> dict:
    index = json.loads((root / "index.json").read_text())
    for name, expected in index["files_sha256"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Evidence hash mismatch: {name}")
    cases = successes = 0
    for entry in index["experiments"]:
        folder = root / entry["directory"]
        protocol = json.loads((folder / "protocol.json").read_text())
        results = json.loads((folder / "results.json").read_text())
        votes = json.loads((folder / "votes.json").read_text())
        if results["protocol_sha256"] != digest(protocol):
            raise ValueError(f"Protocol mismatch: {folder.name}")
        declared = {
            f"{mode}-d{disks}-s{seed}"
            for mode in protocol["modes"]
            for disks in protocol["disks"]
            for seed in protocol["seeds"]
        }
        rows = results["runs"]
        observed = {row["run_id"] for row in rows}
        if len(observed) != len(rows) or not observed <= declared:
            raise ValueError(f"Duplicate or undeclared cases: {folder.name}")
        if entry["complete"] and observed != declared:
            raise ValueError(f"Incomplete protocol: {folder.name}")
        if entry["finished_cases"] != len(rows) or entry["declared_cases"] != len(declared):
            raise ValueError(f"Incorrect index counts: {folder.name}")
        for row in rows:
            if row["mode"] != "whole":
                samples = [s for s in votes["samples"] if s["run_id"] == row["run_id"]]
                decisions = [s for s in votes["steps"] if s["run_id"] == row["run_id"]]
                moves = [json.loads(s["decision"])["action"] for s in decisions]
                if row["calls"] != len(samples) or row["moves"] != moves:
                    raise ValueError(f"Journal mismatch: {folder.name}/{row['run_id']}")
            for sample in votes["samples"]:
                if sample["response"] is not None:
                    actual = hashlib.sha256(sample["response"].encode()).hexdigest()
                    if sample["response_sha"] != actual:
                        raise ValueError(f"Response hash mismatch: {folder.name}")
            evaluation = evaluate_moves(Hanoi(row["disks"]), row["moves"])
            if row["evaluation"] != evaluation:
                raise ValueError(f"Evaluation mismatch: {folder.name}/{row['run_id']}")
            passed = not row.get("error") and evaluation["passed"]
            if row["passed"] != passed:
                raise ValueError(f"Success mismatch: {folder.name}/{row['run_id']}")
            cases += 1
            successes += passed
    return {
        "verified_files": len(index["files_sha256"]),
        "completed_cases_including_pilots": cases,
        "successful_cases": successes,
        "llm_calls_performed_by_verifier": 0,
    }


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parents[1] / "docs/evidence"), indent=2))
