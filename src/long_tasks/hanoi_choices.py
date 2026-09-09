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


class ChoiceHanoi:
    def __init__(self, disks=7, rule_style="positive"):
        if rule_style not in {"negative", "positive"}:
            raise ValueError("Unknown choice rule style")
        self.task = Hanoi(disks)
        self.rule_style = rule_style

    @property
    def specification(self):
        return {
            "task": "hanoi-choice",
            "version": 1,
            "disks": self.task.disks,
            "validation": "all_legal_moves_only",
            "rule_style": self.rule_style,
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
        return (
            "Select exactly one of the listed moves using the rule. All listed moves are physically legal, but only one follows the rule.\n"
            + "RULE: "
            + rule
            + "\nOPTIONS:\n"
            + "\n".join(rows)
            + '\nReturn only JSON {"choice":"A"}, {"choice":"B"}, or {"choice":"C"} for the selected option. /no_think'
        )

    def parse(self, text, state):
        value = strict_json(text)
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
