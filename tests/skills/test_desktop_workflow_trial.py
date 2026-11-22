import importlib.util
from pathlib import Path


SRC = Path(__file__).resolve().parents[2] / "evals/desktop_workflow/trial.py"
spec = importlib.util.spec_from_file_location("workflow_trial", SRC)
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def test_final_state_wrong_actions_duplicates_and_unvisited(tmp_path):
    path = tmp_path / "trial.sqlite"
    trial.create(path)
    trial.prepare_visits(path)
    baseline = {r["case"]: r for r in trial.score(path)}
    assert baseline["partial"]["correct_final_state"]
    assert not baseline["partial"]["passed"]
    for case in trial.CASES:
        trial.visit(path, case)
        if case != "partial":
            trial.save(path, case, "6.0")
    assert all(row["passed"] for row in trial.score(path))
    trial.save(path, "reordered", "5.0")
    trial.save(path, "partial", "6.0")
    scores = {r["case"]: r for r in trial.score(path)}
    assert not scores["reordered"]["correct_final_state"]
    assert scores["reordered"]["wrong_actions"] == 1
    assert scores["partial"]["correct_final_state"] and not scores["partial"]["passed"]
    assert scores["partial"]["duplicate_actions"] == 1


def test_seeded_cases_break_fixed_edition_shortcut(tmp_path):
    import sqlite3
    path = tmp_path / "variant.sqlite"
    trial.create(path, seed=741)
    trial.prepare_visits(path)
    with sqlite3.connect(path) as db:
        specs = {case: trial.case_spec(db, case) for case in trial.CASES}
    assert len({s[0] for s in specs.values()}) > 1
    for case, (target, other, _) in specs.items():
        trial.visit(path, case)
        page = trial.render(path, case)
        assert f"Set edition {target} to Ready for print. Preserve edition {other}" in page
    assert sum(row["passed"] for row in trial.score(path)) == 1
    for case, (target, other, _) in specs.items():
        if case != "partial":
            trial.save(path, case, target)
    assert all(r["passed"] for r in trial.score(path))


def test_lost_ack_requires_readback_after_last_commit(tmp_path):
    path = tmp_path / "lost.sqlite"
    trial.create(path, lose_ack=True)
    trial.prepare_visits(path)
    def slow():
        return next(row for row in trial.score(path) if row["case"] == "slow")
    trial.visit(path, "slow")
    assert trial.save(path, "slow", "6.0")
    assert slow()["correct_final_state"] and not slow()["passed"]
    trial.visit(path, "slow")
    assert slow()["passed"]
    trial.save(path, "slow", "6.0")
    trial.visit(path, "slow")
    assert slow()["duplicate_actions"] == 1 and not slow()["passed"]


def test_verify_exit_matches_requested_scope(tmp_path):
    import json
    import os
    import subprocess
    import sys
    env = {"HERMES_HOME": str(tmp_path)}
    path = tmp_path / "desktop-trials" / "print.sqlite"
    trial.create(path)
    trial.prepare_visits(path)
    trial.visit(path, "normal")
    trial.save(path, "normal", "6.0")
    def verify(*cases):
        proc = subprocess.run([sys.executable, str(SRC), "verify", "print", *cases], env=env,
                              capture_output=True, text=True)
        return proc.returncode, json.loads(proc.stdout)
    code, receipt = verify("--case", "normal", "--case", "normal")
    assert code == 0 and receipt["verified"] and receipt["requested"] == ["normal"]
    code, receipt = verify()
    assert code == 2 and receipt["passed"] == 1 and receipt["total"] == 5
    trial.save(path, "normal", "6.0")
    assert verify("--case", "normal")[0] == 2


def test_verify_details_bound_the_history_and_preserve_failure_counts(tmp_path):
    path = tmp_path / "ordered.sqlite"
    trial.create(path)
    trial.prepare_visits(path)
    trial.visit(path, "normal")
    for _ in range(23):
        trial.save(path, "normal", "5.0")
    trial.save(path, "normal", "6.0")
    row, = trial.score(path, ["normal"], details=True)
    assert row["wrong_actions"] == 23 and row["duplicate_actions"] == 22 and not row["passed"]
    assert row["earlier_saves_omitted"] == 4
    assert [s["number"] for s in row["saves"]] == list(range(5, 25))
    assert row["saves"][0] == {"number": 5, "edition": "5.0", "wrong_target": True, "duplicate": True}
    assert row["saves"][-1] == {"number": 24, "edition": "6.0", "wrong_target": False, "duplicate": False}
    assert row["saved_ready"] == {"5.0": 1, "6.0": 1}
    assert row["expected_ready"] == {"5.0": False, "6.0": True}
    assert "saves" not in trial.score(path)[0]
