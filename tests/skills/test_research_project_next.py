from datetime import date, timedelta
import sqlite3
import pytest
from hermes_cli.theseus_research import library, project_next
from hermes_constants import get_hermes_home


def op(action, project="Packing", **args):
    return library.execute(dict(action=action, project=project, **args))


@pytest.mark.parametrize(
    "text,budget",
    [
        ('In project "Plans" what can I actually finish in 18 minutes?', 18),
        ("What should we work on next in project Packing?", None),
        ("I have 0.5 hours. Is there any recorded work that fits?", 30),
        ("Where did we leave off?", None),
    ],
)
def test_planning_request(text, budget):
    assert project_next.requested(text) == dict(max_minutes=budget)


@pytest.mark.parametrize(
    "text",
    [
        "What would you test next?",
        "What explains the earliest bench tests?",
        "I have a couple of hours. What can I finish?",
        "I have 10 to 15 minutes. What can I finish?",
        "What can I finish in less than 10 minutes?",
        "I have 18 minutes. How many seconds is that?",
        'Explain the sentence "What can I finish in 10 minutes?"',
        "What should I work on next? Don't check my saved records.",
        "What to do about undersized hooks?",
    ],
)
def test_other_requests_do_not_trigger_automatic_planning(text):
    assert project_next.requested(text) is None


def test_counts_feasibility_and_isolation():
    for title, state, minutes in [
        ("Pack slips", "Ready", 12),
        ("Inventory", "Ready", 32),
        ("Weigh box", "Ready", None),
        ("Meet courier", "Waiting", 5),
        ("Send tracking", "Done", 3),
        ("Select cover", "Needs decision", 10),
    ]:
        op(
            "attention_capture",
            title=title,
            state=state,
            minutes=minutes,
            context="Pack orders",
        )
    op(
        "attention_capture",
        "Other",
        title="Private other project",
        state="Ready",
        minutes=1,
        context="Different",
    )
    for title, days in [("Choose shipper", -1), ("Renew bag lease", 9)]:
        op(
            "decision_save",
            title=title,
            choice="Keep current contract",
            reason="Check renewal",
            assumption="Rates hold",
            due=(date.today() + timedelta(days=days)).isoformat(),
        )
    r = op("project_next", max_minutes=15)
    assert [item["title"] for item in r["ready"]] == ["Pack slips"]
    assert [item["title"] for item in r["decisions"]] == ["Choose shipper"]
    assert r["counts"] == dict(
        ready=3,
        eligible=1,
        without_estimate=1,
        over_budget=1,
        waiting=1,
        needs_decision=1,
        due=1,
        workflows=0,
        crawls=0,
    )
    assert r["awaiting_choice"][0]["title"] == "Select cover"
    from pathlib import Path

    assert (
        project_next.automatic(
            Path(get_hermes_home()), "Packing", "What can I finish in 15 minutes?"
        )
        == r
    )
    assert "Private other project" not in str(r)
    assert op("project_next", max_minutes=5)["ready"] == []
    assert r["receipt"] != op("project_next", max_minutes=5)["receipt"]


def test_snapshot_requires_no_registered_tools_and_creates_no_domain_tables():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        r = project_next.snapshot(db, "Empty", 20)
        assert not any(r["counts"].values())
        assert not db.execute("select name from sqlite_master").fetchall()
        with pytest.raises(ValueError):
            project_next.snapshot(db, "Empty", True)
    finally:
        db.close()


def test_checkpoints_are_read_only_and_are_not_timed_commitments():
    b = op(
        "brief_create",
        title="Release procedure",
        question="How did we approve the shipment?",
        columns=["Check"],
        rows=[dict(name="Goods", cells={"Check": dict(value="Verified")})],
    )
    wf = op(
        "workflow_save",
        brief=b["brief"],
        title="Dispatch steps",
        steps="Review address\nInspect package",
        output="Dispatch form",
        review="Verify packaging",
    )["workflows"][0]
    job = op(
        "workflow_start",
        workflow=wf["id"],
        title="Incoming dispatch",
        question="Which parcel?",
    )["jobs"][0]
    op(
        "workflow_advance",
        job=job["id"],
        expected_step=0,
        note="Address checked against invoice",
    )
    c = op(
        "crawl_create",
        url="https://example.org/handbook",
        question="Where is the regulation?",
        pages=2,
        seconds=30,
    )
    op(
        "crawl_checkpoint",
        crawl=c["id"],
        question="Which appendix gives the limit?",
        note="Summary lacks the limit",
    )
    first = op("project_next", max_minutes=10)
    assert (
        not first["ready"]
        and first["counts"]["workflows"] == first["counts"]["crawls"] == 1
    )
    assert first["workflows"][0]["next_step"] == "Inspect package"
    assert first["crawls"][0]["question"] == "Which appendix gives the limit?"
    assert first == op("project_next", max_minutes=10)
    assert op("crawl_status", crawl=c["id"])["state"] == "paused"
    assert len(op("workflow_list")["jobs"][0]["progress"]) == 1


@pytest.mark.parametrize(
    "duration,budget",
    [
        ("a quarter of an hour", 15),
        ("half an hour", 30),
        ("a half-hour", 30),
        ("one hour and fifteen minutes", 75),
        ("twenty-five minutes", 25),
        ("two hours", 120),
        ("between 15 and 30 minutes", None),
        ("fifteen or thirty minutes", None),
        ("roughly half an hour", None),
        ("under twenty minutes", None),
        ("one and a half hours", None),
        ("15 minutes today and 25 minutes tomorrow", None),
    ],
)
def test_spoken_and_uncertain_time_windows(duration, budget):
    actual = project_next.requested(
        "I have " + duration + ". What can I actually finish?"
    )
    assert actual == ({"max_minutes": budget} if budget is not None else None)
