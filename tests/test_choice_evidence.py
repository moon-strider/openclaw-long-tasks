import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_choices", ROOT / "scripts/verify_choices.py")
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def test_archived_model_votes_replay():
    report = VERIFIER.verify_archive(ROOT / "docs/evidence/hundred")
    assert report["new_llm_calls"] == 0
    assert report["experiments"]["runs/qwen-two-fixed"] == {
        "cases": 4,
        "successes": 0,
        "calls": 72,
    }


@pytest.mark.parametrize("field", ["votes", "evaluation"])
def test_semantic_replay_rejects_altered_claims(tmp_path, field):
    folder = tmp_path / "case"
    shutil.copytree(ROOT / "docs/evidence/hundred/runs/qwen-two-fixed", folder)
    if field == "votes":
        path = folder / "journal.json"
        data = json.loads(path.read_text())
        votes = json.loads(data["steps"][0]["votes"])
        votes[next(iter(votes))] += 1
        data["steps"][0]["votes"] = json.dumps(votes)
        message = "Vote count mismatch"
    else:
        path = folder / "results.json"
        data = json.loads(path.read_text())
        data["runs"][0]["evaluation"]["correct_prefix"] = 127
        message = "Independent evaluation mismatch"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=message):
        VERIFIER.verify_experiment(folder)
