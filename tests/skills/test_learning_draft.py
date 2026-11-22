import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


source = Path(__file__).resolve().parents[2] / "skills/software-development/learning-run/scripts/draft.py"
spec = importlib.util.spec_from_file_location("learning_draft", source)
draft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(draft)


def test_selection_prioritizes_explicit_references_and_reverts():
    events = [dict(run=i, problem="print", phase="evaluation",
                   details={"candidate": "result", "diff": "original patch", "recommendation": "revert" if i == 2 else "keep"})
              for i in range(1, 10)]
    events[1]["patch_correction_runs"] = [8]
    shown, omitted = draft.prior_outcomes(events, [1])
    assert [e["run"] for e in shown] == [1, 2, 9, 8, 7, 6] and omitted == 3
    assert shown[1]["patch_correction_runs"] == [8] and "diff" not in shown[1]


def test_draft_uses_prior_hypothesis_and_keeps_reserved_cases_in_journal():
    phases = [
        ("proposal", {"change": "Verify the export", "rationale": "The save can be retried",
                      "expected": "One complete file"}),
        ("evaluation", {"candidate": "One file on development", "fresh": "reserved-case-7",
                        "recommendation": "keep"}),
        ("review", {"decision": "revert", "reason": "Review of reserved-case-7"}),
    ]
    events = [dict(run=1, problem="export", phase=phase, details=details)
              for phase, details in phases]
    shown, _ = draft.prior_outcomes(events, [])
    assert shown[0]["rationale"] == "The save can be retried"
    assert shown[0]["expected"] == "One complete file"
    assert shown[0]["candidate"] == "One file on development"
    assert shown[0]["decision"] == "revert"
    assert "reserved-case-7" not in json.dumps(shown)
    assert events[1]["details"]["fresh"] == "reserved-case-7"


@pytest.mark.parametrize("cited,allowed", [([12], True), ([], True), ([11], False), ([True], False), ([12, 12], False)])
def test_only_real_evidence_ids_can_be_cited(monkeypatch, cited, allowed):
    answer = {"change": "use the receipt", "rationale": "lost acknowledgement",
              "expected": "one write", "used_prior": cited}
    completed = {"type": "result", "exit_code": 0, "session_id": "test", "text": json.dumps(answer)}
    monkeypatch.setattr(draft.subprocess, "run", lambda *args, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(completed)))
    if allowed:
        assert draft.suggest("print", "saved twice", [{"run": 12}], Path("theseus")) == (answer, "test", False)
    else:
        with pytest.raises(ValueError, match="cited"):
            draft.suggest("print", "saved twice", [{"run": 12}], Path("theseus"))
