"""A small Hanoi adapter. The oracle below is for evaluation, never vote selection."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .maker import Candidate, strict_json

RULES = (
    "Solve Towers of Hanoi by the iterative algorithm. Pegs are 0, 1, 2. "
    "Disks are numbered smallest=1. Each peg array is ordered bottom-to-top. "
    "Move all disks from peg 0 to peg 2. Move exactly one top disk at a time, "
    "never placing a larger disk on a smaller disk. "
    "For an ODD number of disks, disk 1 follows the cycle 0 -> 2 -> 1 -> 0. "
    "For an EVEN number of disks, disk 1 follows 0 -> 1 -> 2 -> 0. "
    "On odd-numbered moves move disk 1 along its cycle. "
    "On even-numbered moves make the only legal move between the two pegs "
    "that do not contain disk 1."
    " Update the arrays by removing the moved disk from the END of its source array "
    "and appending that disk to the END of its destination array. Every other disk "
    "stays in its original array. Peg numbers are array indexes, never disk values."
)


def apply_move(pegs: list[list[int]], move: list[int]) -> list[list[int]]:
    if not isinstance(move, list) or len(move) != 3 or any(type(v) is not int for v in move):
        raise ValueError("move must contain three integers")
    disk, source, target = move
    if source not in range(3) or target not in range(3) or source == target:
        raise ValueError("invalid pegs")
    if not pegs[source] or pegs[source][-1] != disk:
        raise ValueError("disk is not on top of its source")
    if pegs[target] and pegs[target][-1] < disk:
        raise ValueError("larger disk on smaller disk")
    result = [list(p) for p in pegs]
    result[source].pop()
    result[target].append(disk)
    return result


@dataclass(frozen=True)
class Hanoi:
    disks: int = 3
    state_mode: str = "model"
    prompt_mode: str = "full"

    def __post_init__(self):
        if self.prompt_mode not in {"full", "phase", "micro"}:
            raise ValueError("prompt_mode must be full, phase or micro")
        if self.state_mode not in {"model", "deterministic"}:
            raise ValueError("state_mode must be model or deterministic")
        if self.prompt_mode in {"phase", "micro"} and self.state_mode != "deterministic":
            raise ValueError("phase prompts require deterministic state updates")
        if type(self.disks) is not int or not 1 <= self.disks <= 20:
            raise ValueError("Use 1–20 disks")

    @property
    def specification(self):
        return {
            "task": "hanoi",
            "version": 4,
            "state_mode": self.state_mode,
            "prompt_mode": self.prompt_mode,
            "disks": self.disks,
            "validation": "legal_move_and_state_consistency",
        }

    @property
    def initial_state(self):
        return {"pegs": [list(range(self.disks, 0, -1)), [], []], "step": 0}

    @property
    def total_steps(self):
        return 2**self.disks - 1

    def prompt(self, state):
        if self.prompt_mode == "micro":
            if state["step"] % 2 == 0:
                source = next(i for i, peg in enumerate(state["pegs"]) if 1 in peg)
                cycle = "0 -> 2 -> 1 -> 0" if self.disks % 2 else "0 -> 1 -> 2 -> 0"
                instruction = (
                    f"Disk 1 is on peg {source}. Move it one position forward in the cycle {cycle}. "
                    f"The source peg is {source}. What is the next peg in this cycle? "
                )
            else:
                available = [
                    (i, peg[-1] if peg else None)
                    for i, peg in enumerate(state["pegs"])
                    if 1 not in peg
                ]
                instruction = "Choose the legal move between these two pegs: "
                for peg, disk in available:
                    instruction += (
                        f"peg {peg} is empty; "
                        if disk is None
                        else f"peg {peg} has top disk {disk}; "
                    )
                instruction += (
                    "If one peg is empty, move the other peg's top disk there. "
                    "Otherwise move the smaller-numbered top disk onto the larger-numbered one. "
                )
            return (
                instruction
                + 'Return ONLY JSON {"move":[disk,source_peg,target_peg]} using integers. /no_think'
            )
        if self.prompt_mode == "phase":
            if self.state_mode != "deterministic":
                raise ValueError("phase prompts require deterministic state updates")
            if state["step"] % 2 == 0:
                cycle = "0 -> 2 -> 1 -> 0" if self.disks % 2 else "0 -> 1 -> 2 -> 0"
                instruction = "Move disk 1 one position forward along the peg cycle " + cycle + "."
            else:
                available = [i for i, peg in enumerate(state["pegs"]) if 1 not in peg]
                instruction = (
                    f"Move a top disk between peg {available[0]} and peg {available[1]}. "
                    "Move from the nonempty peg to the empty peg if one is empty. "
                    "If both are nonempty, move the smaller top disk onto the larger top disk."
                )
            return (
                "Execute one Towers of Hanoi move. Peg numbers are 0, 1, 2. "
                "Disk 1 is the smallest. Each peg array is bottom-to-top; its last entry is the top disk.\n"
                + "Pegs: "
                + json.dumps(state["pegs"])
                + ".\n"
                + instruction
                + '\nReturn ONLY JSON {"move":[disk,source_peg,target_peg]} '
                + "with integer values. No explanation or markdown. /no_think"
            )
        if self.state_mode == "deterministic":
            return (
                RULES
                + f"\nThere are {self.disks} disks. Next move number: {state['step'] + 1}.\n"
                + "Current pegs (bottom-to-top): "
                + json.dumps(state["pegs"])
                + ".\n"
                + 'Choose exactly ONE next move. Return only JSON {"move":[disk,source,target]}. '
                + "Use integer values, not strings. No markdown or explanation. /no_think"
            )
        positions = [
            next(peg for peg, stack in enumerate(state["pegs"]) if disk in stack)
            for disk in range(1, self.disks + 1)
        ]
        return (
            "Solve Towers of Hanoi. Pegs: 0, 1, 2. Disks: 1 (smallest) through "
            + str(self.disks)
            + " (largest). Move every disk from peg 0 to peg 2. "
            "Move one disk at a time, never a larger disk onto a smaller disk. "
            "A disk can move only if no smaller disk is on its peg. "
            "Use this iterative algorithm: on ODD-numbered moves, move disk 1 along "
            + ("0 -> 2 -> 1 -> 0. " if self.disks % 2 else "0 -> 1 -> 2 -> 0. ")
            + "On EVEN-numbered moves, make the only legal move between the two pegs without disk 1. "
            "Execute ONE move.\n"
            + f"Next move number: {state['step'] + 1}.\n"
            + "Current positions: "
            + json.dumps(positions)
            + ". "
            + "positions[i-1] is the peg of disk i.\n"
            + 'Return ONLY JSON {"move":[disk,source,target],"positions":[...new positions...]}. '
            "Change only the moved disk in positions; copy the other entries unchanged. "
            "No markdown fences or explanation. /no_think"
        )

    def parse(self, text, state):
        value = strict_json(text)
        if self.state_mode == "deterministic":
            if not isinstance(value, dict) or set(value) != {"move"}:
                raise ValueError("wrong response keys")
            actual = apply_move(state["pegs"], value["move"])
            return Candidate(value["move"], {"pegs": actual, "step": state["step"] + 1})
        if not isinstance(value, dict) or set(value) != {"move", "positions"}:
            raise ValueError("wrong response keys")
        positions = value["positions"]
        if (
            not isinstance(positions, list)
            or len(positions) != self.disks
            or any(type(p) is not int or p not in range(3) for p in positions)
        ):
            raise ValueError("invalid positions")
        actual = [[d for d in range(self.disks, 0, -1) if positions[d - 1] == p] for p in range(3)]
        expected = apply_move(state["pegs"], value["move"])
        if expected != actual:
            raise ValueError("reported state differs from applying the move")
        return Candidate(value["move"], {"pegs": actual, "step": state["step"] + 1})

    def completed(self, state):
        return state["pegs"] == [[], [], list(range(self.disks, 0, -1))]

    def whole_prompt(self):
        return (
            RULES
            + f"\nNumber of disks: {self.disks}. Initial pegs: "
            + json.dumps(self.initial_state["pegs"])
            + f". Return all {self.total_steps} moves in order. "
            + 'Reply only with JSON {"moves": [[disk, source, target], ...]}. '
            + "No markdown or commentary. /no_think"
        )


def oracle_moves(disks: int, source=0, target=2, spare=1):
    if disks:
        yield from oracle_moves(disks - 1, source, spare, target)
        yield [disks, source, target]
        yield from oracle_moves(disks - 1, spare, target, source)


def evaluate_moves(task: Hanoi, moves: list) -> dict:
    pegs = task.initial_state["pegs"]
    correct_prefix = 0
    error = None
    reference = oracle_moves(task.disks)
    for index, move in enumerate(moves):
        expected = next(reference, None)
        if move == expected and correct_prefix == index:
            correct_prefix += 1
        try:
            pegs = apply_move(pegs, move)
        except (ValueError, TypeError) as exc:
            error = str(exc)
            break
    exact = correct_prefix == task.total_steps and len(moves) == task.total_steps
    return {
        "passed": exact and task.completed({"pegs": pegs}),
        "correct_prefix": correct_prefix,
        "expected_steps": task.total_steps,
        "observed_steps": len(moves),
        "legal": error is None,
        "error": error,
        "final_pegs": pegs,
    }
