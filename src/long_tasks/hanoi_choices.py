"""Hanoi action selection; every generally legal move remains available."""

from .hanoi import Hanoi, apply_move
from .maker import Candidate, strict_json

CHOICE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "choice",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"choice": {"type": "string", "enum": ["A", "B", "C"]}},
            "required": ["choice"],
            "additionalProperties": False,
        },
    },
}

DESTINATION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "destination",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"destination": {"type": "integer", "enum": [0, 1, 2]}},
            "required": ["destination"],
            "additionalProperties": False,
        },
    },
}


class ChoiceHanoi:
    def __init__(self, disks=7, rule_style="positive", routing_style="mapping"):
        if rule_style not in {"negative", "positive"}:
            raise ValueError("Unknown choice rule style")
        if routing_style not in {"mapping", "examples", "lookup"}:
            raise ValueError("Unknown routing style")
        self.task = Hanoi(disks)
        self.rule_style = rule_style
        self.routing_style = routing_style

    @property
    def specification(self):
        return {
            "task": "hanoi-choice",
            "version": 2,
            "disks": self.task.disks,
            "validation": "all_legal_moves_only",
            "rule_style": self.rule_style,
            "routing_style": self.routing_style,
        }

    @property
    def initial_state(self):
        return self.task.initial_state

    @property
    def total_steps(self):
        return self.task.total_steps

    def completed(self, state):
        return self.task.completed(state)

    def options(self, state):
        actions = []
        for source, peg in enumerate(state["pegs"]):
            if not peg:
                continue
            for target in range(3):
                move = [peg[-1], source, target]
                try:
                    apply_move(state["pegs"], move)
                except ValueError:
                    continue
                actions.append(move)
        return dict(zip("ABC", actions, strict=False))

    def prompt(self, state):
        if self.routing_style == "lookup" and state["step"] % 2 == 0:
            source = next(i for i, peg in enumerate(state["pegs"]) if 1 in peg)
            mapping = "{0: 2, 1: 0, 2: 1}" if self.task.disks % 2 else "{0: 1, 1: 2, 2: 0}"
            return (
                f"What is the value of d[{source}] in the Python dictionary d = {mapping}? "
                "Return JSON with the integer value in the destination field."
            )
        options = self.options(state)
        if state["step"] % 2 == 0:
            mapping = (
                "0 goes to 2; 1 goes to 0; 2 goes to 1"
                if self.task.disks % 2
                else "0 goes to 1; 1 goes to 2; 2 goes to 0"
            )
            rule = (
                "Choose a move of DISK 1. Its source and destination MUST match this mapping: "
                + mapping
                + "."
            )
        else:
            rule = "Choose the move whose DISK is NOT 1. Do not choose a move of disk 1."
        if state["step"] % 2 and self.rule_style == "positive":
            rule = "Compare the DISK numbers in all options. Choose the option with the LARGEST DISK number."
        rows = [
            f"{letter}: DISK {m[0]}, SOURCE {m[1]}, DESTINATION {m[2]}"
            for letter, m in options.items()
        ]
        prompt = (
            "Select exactly one of the listed moves using the rule. All listed moves are physically legal, but only one follows the rule.\n"
            + "RULE: "
            + rule
            + "\nOPTIONS:\n"
            + "\n".join(rows)
            + '\nReturn only JSON {"choice":"A"}, {"choice":"B"}, or {"choice":"C"} for the selected option. /no_think'
        )
        if self.routing_style == "examples" and state["step"] % 2 == 0:
            # Fixed demonstrations, independent of the current state and oracle.
            answers = (
                ("B", "0 goes to 2", "C", "2 goes to 1", "A", "1 goes to 0")
                if self.task.disks % 2
                else ("A", "0 goes to 1", "B", "2 goes to 0", "B", "1 goes to 2")
            )
            prompt = (
                "Worked examples of the disk-one routing rule:\n"
                "Example 1 options: A: disk 1 from 0 to 1; B: disk 1 from 0 to 2. "
                f"Correct choice {answers[0]}, because {answers[1]}.\n"
                "Example 2 options: A: disk 8 from 1 to 0; B: disk 1 from 2 to 0; C: disk 1 from 2 to 1. "
                f"Correct choice {answers[2]}, because {answers[3]}.\n"
                "Example 3 options: A: disk 1 from 1 to 0; B: disk 1 from 1 to 2; C: disk 8 from 2 to 0. "
                f"Correct choice {answers[4]}, because {answers[5]}.\n"
                "Now solve the following input using the same rule:\n" + prompt
            )
        return prompt

    def parse(self, text, state):
        value = strict_json(text)
        if self.routing_style == "lookup" and state["step"] % 2 == 0:
            if (
                not isinstance(value, dict)
                or set(value) != {"destination"}
                or type(value["destination"]) is not int
            ):
                raise ValueError("Expected one integer destination")
            source = next(i for i, peg in enumerate(state["pegs"]) if 1 in peg)
            action = [1, source, value["destination"]]
            return Candidate(
                action, {"pegs": apply_move(state["pegs"], action), "step": state["step"] + 1}
            )
        if (
            not isinstance(value, dict)
            or set(value) != {"choice"}
            or not isinstance(value["choice"], str)
        ):
            raise ValueError("Expected one choice")
        options = self.options(state)
        if value["choice"] not in options:
            raise ValueError("Unavailable choice")
        action = options[value["choice"]]
        return Candidate(
            action, {"pegs": apply_move(state["pegs"], action), "step": state["step"] + 1}
        )
