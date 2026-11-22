"""Independent grading, replay and operator-approval contracts for Desktop trials."""

import copy
import json
import sqlite3
from contextlib import closing

import pytest

from hermes_cli.theseus_research import pilot, pilot_cases, pilot_policy, routing


def check(shape=True, value=True, receipts=True):
    return {
        "shape": shape,
        "projects": {"sample": {"value": value, "receipts": receipts}},
    }


@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_development_and_held_out_are_distinct_and_score_against_original_receipts(
    difficulty,
):
    development, held = pilot_cases.roster(17, "condition", difficulty)
    seen = set()
    for case in [development, *held]:
        answer = case["expected"]
        ids = {row["id"] for row in case["documents"]}
        assert not seen.intersection(ids)
        seen.update(ids)
        assert all(set(v["receipts"]) <= ids for v in answer.values())
        assert sum(pilot.check_bits(pilot_cases.score(case, answer))) == 7
        prompt = pilot_cases.challenge(case)
        assert '"expected"' not in prompt
        assert all(row["id"] in prompt for row in case["documents"])
        numeric = next(p for p, row in answer.items() if row["value"] is not None)
        for invalid in (True, str(answer[numeric]["value"])):
            modified = copy.deepcopy(answer)
            modified[numeric]["value"] = invalid
            assert not pilot_cases.score(case, modified)["projects"][numeric]["value"]
        modified = copy.deepcopy(answer)
        modified[numeric]["receipts"] *= 2
        assert not pilot_cases.score(case, modified)["projects"][numeric]["receipts"]


def test_drafting_differs_only_in_admitted_history():
    dev, _ = pilot_cases.roster(113)
    baseline = {"response": dev["expected"]}

    def packet(history):
        return json.loads(
            pilot.proposal_prompt(dev, baseline, history).split("\n", 1)[1]
        )

    historic, control = packet({"3": {"recommendation": "revert"}}), packet({})
    assert historic.pop("prior_experiments") and not control.pop("prior_experiments")
    assert historic == control
    obj = {
        "rule": "Use the signed date.",
        "rationale": "Resolve scope.",
        "used_prior": [42],
    }
    with pytest.raises(ValueError, match="not given"):
        pilot.validate_proposal(obj, {})
    assert pilot.validate_proposal(obj, {"42": {}}) == obj


def test_aggregate_gain_cannot_hide_regression_and_tie_cannot_be_applied():
    result = pilot.compare(
        {
            "current": [check(True, False, False)],
            "without_history": [check(False, True, True)],
            "with_history": [check(True, False, False)],
        }
    )
    assert result["scores"]["without_history"] == 2 and result["winner"] == "current"
    result = pilot.compare(
        {
            "current": [check(receipts=False)],
            "without_history": [check()],
            "with_history": [check()],
        }
    )
    assert result["tied_candidates"]


def test_cached_turn_rechecks_original_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "get_hermes_home", lambda: tmp_path)
    with closing(sqlite3.connect(tmp_path / "state.db")) as db, db:
        db.executescript(
            "CREATE TABLE sessions(id,model); CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id,role,content,tool_calls);"
        )
        db.execute("INSERT INTO sessions VALUES('test',?)", (pilot.MODEL,))
        db.executemany(
            "INSERT INTO messages(session_id,role,content) VALUES('test',?,?)",
            [("user", "fresh"), ("assistant", '{"value":12}')],
        )
    path = tmp_path / "call_test.json"
    entry = {
        "session": "test",
        "fingerprint": pilot.digest([pilot.MODEL, "fresh"]),
        "response": {"value": 12},
    }
    pilot.write(path, entry)
    assert pilot.model_call(tmp_path, {}, "call_test", "fresh") == entry
    pilot.write(path, {**entry, "response": {"value": 13}})
    with pytest.raises(ValueError, match="cached answer"):
        pilot.model_call(tmp_path, {}, "call_test", "fresh")
    with closing(sqlite3.connect(tmp_path / "state.db")) as db, db:
        db.execute("UPDATE messages SET tool_calls='[{}]' WHERE role='assistant'")
    with pytest.raises(ValueError, match="trace did not verify"):
        pilot.trace_reply("test", "fresh", "call_test")


def test_worker_lock_prevents_second_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "get_hermes_home", lambda: tmp_path)
    root = pilot.workspace()
    with (root / "worker.lock").open("a+") as lock:
        pilot.fcntl.flock(lock, pilot.fcntl.LOCK_EX)
        assert pilot.action("pilot_start") == {"status": "starting"}
        with pytest.raises(ValueError, match="Wait"):
            pilot.action("pilot_restore")


def test_approval_verifies_registered_artifacts_and_restores_previous_policy(
    tmp_path, monkeypatch
):
    """Controlled fixture uses the real journal and real Desktop backend, with no live approval."""
    monkeypatch.setattr(pilot, "get_hermes_home", lambda: tmp_path)
    root = pilot.workspace()
    key = "pilot-" + ("7" * 24)
    state = pilot.initialise(root, key, None)
    pilot.write(root / "latest.json", {"key": key})
    folder = root / key
    pilot.admit(folder, state)
    run = state["run"]
    rule = "Identify the effective date before applying a correction."
    proposal = {
        "rule": rule,
        "rationale": "Separate effective dates.",
        "used_prior": [],
    }
    scores = {
        "current": [check(value=False)],
        "without_history": [check(value=False)],
        "with_history": [check()],
    }
    result = pilot.compare(scores)
    audit = {
        "run": run,
        "suite_digest": state["suite_digest"],
        "protocol": state["protocol"],
        "result": result,
        "checks_by_arm": scores,
        "proposals": {"with_history": proposal},
    }
    pilot.write(folder / "comparison.json", audit)
    state.update(status="complete", result=result)
    pilot.write(folder / "state.json", state)
    pilot.journal(
        "propose",
        run,
        "--change",
        rule,
        "--rationale",
        "test",
        "--expected",
        "one gain",
    )
    pilot.journal(
        "evaluate",
        run,
        "--candidate",
        "fixture",
        "--fresh",
        "fixture",
        "--recommendation",
        "keep",
        "--file",
        "results",
        folder / "comparison.json",
        "--file",
        "suite",
        folder / "suite.json",
        "--diff",
        "-",
        stdin="--- current\n+++ candidate\n@@ -1 +1 @@\n-old\n+new\n",
    )
    assert pilot.action("pilot_status")["can_apply"]
    pilot.write(folder / "comparison.json", {**audit, "run": 99})
    with pytest.raises(ValueError, match="evidence changed"):
        pilot.action("pilot_apply", key, head=0)
    pilot.write(folder / "comparison.json", audit)
    active = pilot.action("pilot_apply", key, head=0)["active"]
    assert active["rule"] == rule and active["action"] == "apply"
    assert not pilot.action("pilot_status")["can_apply"]
    assert rule in routing.compare_context(
        ["North", "South"], pilot_policy.current(tmp_path)["rule"]
    )
    with pytest.raises(ValueError, match="changed"):
        pilot.action("pilot_apply", key, head=0)
    restored = pilot.action("pilot_restore", key, head=active["id"])["active"]
    assert restored["rule"] == "" and restored["action"] == "restore"
    with closing(sqlite3.connect(tmp_path / "learning-runs/history.sqlite")) as db:
        assert db.execute("SELECT count(*) FROM pilot_policy_events").fetchone()[0] == 2
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE run=? AND phase='review'", (run,)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "remaining,difficulty,winner,tied,follow",
    [
        (0, 1, "current", False, False),
        (1, 1, "current", False, True),
        (1, 2, "without_history", True, True),
        (1, 2, "with_history", False, False),
        (1, 3, "current", False, False),
        (1, 3, "without_history", True, False),
    ],
)
def test_automatic_follow_up_requires_permission_and_a_new_case_family(
    remaining, difficulty, winner, tied, follow
):
    state = {"rounds_left": remaining, "difficulty": difficulty}
    result = {"winner": winner, "tied_candidates": tied}
    assert pilot.follow_up_decision(state, result)[0] is follow
