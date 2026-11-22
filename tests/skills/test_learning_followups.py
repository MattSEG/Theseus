import json
from pathlib import Path

import pytest

from hermes_cli.theseus_research import followups, pilot
from hermes_cli.theseus_research.library import connection, execute
from hermes_cli.theseus_research.plugin import desktop_request


@pytest.fixture
def research(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        followups,
        "latest_benchmark",
        lambda kind: (
            27,
            0,
            {
                "model": "Sol",
                "values_correct": 3,
                "arm": "current",
                "report_sha256": "fixture",
            },
        ),
    )

    def call(action, project="Ops", **fields):
        request = dict(action=action, project=project, **fields)
        return (
            desktop_request(request)
            if action.startswith("prediction_")
            else execute(request)
        )

    return call


def setup_watch(call, title="Future", rehearsal=False):
    s = call(
        "capture",
        title="Signed",
        kind="note",
        origin="record:signed",
        text="Current budget USD 27.",
    )
    fields = dict(
        title=title,
        row="North",
        column="Price",
        kind="revision",
        edition=s["edition"],
        quote="Current budget USD 27.",
        expected="USD 27",
        rehearsal=rehearsal,
    )
    call("prediction_freeze", **fields)
    return fields


def brief(call, title, value):
    return call(
        "brief_create",
        title=title,
        question="Which budget?",
        rows=[dict(name="North", cells={"Price": {"value": value}})],
    )


def test_first_export_records_wrong_and_replay_preserves_it(research, tmp_path):
    fields = setup_watch(research)
    with pytest.raises(ValueError, match="already"):
        research("prediction_freeze", **fields)
    b = brief(research, "Future", "USD 21")
    with pytest.raises(ValueError, match="before"):
        research("prediction_freeze", **fields)
    research("brief_export", brief=b["brief"])
    saved = research("prediction_list")["predictions"][0]
    assert saved["receipt"]["actual"] == "USD 21" and saved["outcome"] == "Contradicted"
    research(
        "brief_edit",
        brief=b["brief"],
        expected_revision=1,
        rows=[dict(name="North", cells={"Price": {"value": "USD 27"}})],
    )
    research("brief_export", brief=b["brief"])
    assert research("prediction_list")["predictions"][0] == saved
    assert not research("prediction_list", project="Another")["predictions"]
    evidence = followups.next_counterexample(tmp_path, 0)
    assert evidence == dict(sequence=1, run=27, kind="revision")
    root = pilot.workspace()
    s = pilot.initialise(root, "first", None)
    assert s["counterexample"] == evidence and s["difficulty"] == 2
    s["status"] = "failed"
    pilot.write(root / "first/state.json", s)
    retry = pilot.initialise(root, "retry", "first")
    assert retry["counterexample"] == evidence
    retry["status"] = "complete"
    pilot.write(root / "retry/state.json", retry)
    assert not pilot.initialise(root, "next", "retry")["counterexample"]


@pytest.mark.parametrize(
    "change,expected",
    [
        ("none", "Confirmed"),
        ("source", "Inconclusive"),
        ("missing", "Contradicted"),
        ("policy", "Inconclusive"),
        ("orphan", "Inconclusive"),
    ],
)
def test_outcomes_and_rehearsal_isolation(
    research, tmp_path, monkeypatch, change, expected
):
    setup_watch(research, rehearsal=True)
    b = brief(research, "Future", "" if change == "missing" else "USD 27")
    if change == "source":
        research(
            "capture",
            kind="note",
            title="New",
            origin="record:signed",
            text="New budget USD 44.",
        )
    if change == "policy":
        monkeypatch.setattr(followups.pilot_policy, "current", lambda home: {"id": 2})
    if change == "orphan":
        p = tmp_path / "research/exports"
        p.mkdir()
        (p / f"brief-{b['brief']}-r1.csv").write_text("former export")
        research(
            "brief_edit",
            brief=b["brief"],
            expected_revision=1,
            rows=[dict(name="North", cells={"Price": {"value": "USD 26"}})],
        )
    research("brief_export", brief=b["brief"])
    assert research("prediction_list")["predictions"][0]["outcome"] == expected
    assert followups.next_counterexample(tmp_path, 0) is None


def test_forecast_hidden_from_model_and_quote_checks(research):
    args = setup_watch(research)
    with pytest.raises(ValueError):
        execute(dict(action="prediction_list", project="Ops"))
    with pytest.raises(ValueError, match="passage"):
        research(
            "prediction_freeze", **{**args, "title": "Another", "expected": "USD 38"}
        )


def test_only_verified_exports_and_cited_reviews_are_nominated(research):
    s = research("capture", kind="note", title="Filed", text="USD 27")
    b = research(
        "brief_create",
        title="Reviewed",
        question="What is filed?",
        rows=[
            dict(
                name="North",
                cells={
                    "Price": dict(value="USD 29", edition=s["edition"], quote="USD 27")
                },
            )
        ],
    )
    research(
        "evidence_review",
        brief=b["brief"],
        revision=1,
        row="North",
        column="Price",
        layer="Observation",
        verdict="Contradicts",
        reason="Transcribed the amount incorrectly.",
    )
    assert research("prediction_list")["examples"] == []
    path = research("brief_export", brief=b["brief"])["path"]
    (example,) = research("prediction_list")["examples"]
    assert (
        example["reason"]
        and example["value"] == "USD 29"
        and example["quote"] == "USD 27"
    )
    with connection() as db:
        assert followups.reviewed_examples(db, "Elsewhere") == []
    dev, _ = pilot.pilot_cases.roster(77)
    for history in ({}, {"27": {"prior": "example"}}):
        prompt = pilot.proposal_prompt(dev, {"response": {}}, history, example)
        assert json.loads(prompt.split("\n", 1)[1])["reviewed_work_example"] == example
    Path(path).write_text("altered")
    assert research("prediction_list")["examples"] == []


def test_field_calibration_separates_inconclusive_and_excludes_rehearsals(
    research, tmp_path
):
    setup_watch(research, "Ordinary")
    setup_watch(research, "Practice", rehearsal=True)
    for title in ("Ordinary", "Practice"):
        report = brief(research, title, "USD 27")
        research("brief_export", brief=report["brief"])
    assert followups.calibration(tmp_path, [27]) == {
        "27": {"revision": {"Confirmed": 1}}
    }
    assert not followups.calibration(tmp_path, [28])


def test_benchmark_mixed_values_abstain_even_when_format_and_citations_pass():
    case = {
        "shape": True,
        "projects": {
            "One": {"value": True, "receipts": True},
            "Two": {"value": False, "receipts": True},
        },
    }
    prediction = followups.forecasts(["condition"], {"current": [case]})["current"][
        "condition"
    ]
    assert prediction == {"prediction": "abstain", "correct": 1, "tested": 2}


def test_withdraw_damaged_evidence_from_later_learning_but_preserve_original_verdict(
    research, tmp_path
):
    setup_watch(research)
    item = brief(research, "Future", "USD 29")
    file = research("brief_export", brief=item["brief"])["path"]
    assert followups.next_counterexample(tmp_path, 0)
    Path(file).write_text("replacement")
    assert not followups.next_counterexample(tmp_path, 0)
    assert not followups.calibration(tmp_path, [27])
    assert research("prediction_list")["predictions"][0]["outcome"] == "Contradicted"
