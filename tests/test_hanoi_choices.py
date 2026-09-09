import json

import pytest

from long_tasks.hanoi import apply_move, oracle_moves
from long_tasks.hanoi_choices import ChoiceHanoi


def test_wrong_legal_move_is_available_and_accepted():
    task = ChoiceHanoi(7)
    state = task.initial_state
    assert task.options(state) == {"A": [1, 0, 1], "B": [1, 0, 2]}
    candidate = task.parse('{"choice":"A"}', state)
    assert candidate.action == [1, 0, 1]
    assert candidate.action != next(oracle_moves(7))


def test_options_cover_all_legal_moves_without_using_optimality():
    task = ChoiceHanoi(4)
    state = task.initial_state
    for correct in oracle_moves(4):
        options = task.options(state)
        all_legal = []
        for disk in range(1, 5):
            for source in range(3):
                for target in range(3):
                    move = [disk, source, target]
                    try:
                        apply_move(state["pegs"], move)
                    except ValueError:
                        continue
                    all_legal.append(move)
        assert sorted(options.values()) == sorted(all_legal)
        assert len(options) in {2, 3}
        assert "OPTIONS:" in task.prompt(state)
        for letter, move in options.items():
            assert task.parse(json.dumps({"choice": letter}), state).action == move
        state = {"pegs": apply_move(state["pegs"], correct), "step": state["step"] + 1}
    assert task.completed(state)


@pytest.mark.parametrize("text", ['{"choice":"Z"}', '{"choice":true}', '{"choice":"A","extra":1}'])
def test_malformed_choices_are_rejected(text):
    task = ChoiceHanoi()
    with pytest.raises(ValueError):
        task.parse(text, task.initial_state)


def test_demonstrations_do_not_change_options_or_validate_routing():
    task = ChoiceHanoi(7, routing_style="examples")
    state = task.initial_state
    assert task.options(state) == ChoiceHanoi(7).options(state)
    assert task.parse('{"choice":"A"}', state).action == [1, 0, 1]
    assert "Worked examples" in task.prompt(state)
    assert task.specification != ChoiceHanoi(7).specification
    even = ChoiceHanoi(4, routing_style="examples")
    assert "Correct choice A, because 0 goes to 1" in even.prompt(even.initial_state)


def test_lookup_routes_the_model_destination_without_correcting_it():
    task = ChoiceHanoi(7, routing_style="lookup")
    state = task.initial_state
    assert "d[0]" in task.prompt(state)
    wrong = task.parse('{"destination":1}', state)
    assert wrong.action == [1, 0, 1]
    assert wrong.action != next(oracle_moves(7))
    assert "largest" in task.prompt(wrong.state).lower()
    with pytest.raises(ValueError):
        task.parse('{"destination":0}', state)
    with pytest.raises(ValueError):
        task.parse('{"destination":true}', state)
