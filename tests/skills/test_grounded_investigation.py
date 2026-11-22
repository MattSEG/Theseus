from pathlib import Path
from importlib import import_module
import hashlib
import json
import sqlite3
import sys

import pytest

from hermes_cli import theseus_investigation as lookup


memory = import_module("skills.research.revisable-memory.scripts.memory")
reviewer = import_module(
    "skills.software-development.learning-run.scripts.reviewer_trials"
)


@pytest.fixture
def investigation(tmp_path):
    store = memory.Memory(tmp_path, "pump")
    db = store.db
    db.executescript("""
        CREATE TABLE names(name TEXT,anchor TEXT);
        CREATE TABLE docs(id INTEGER PRIMARY KEY,name TEXT,path TEXT,digest TEXT,data BLOB);
        CREATE TABLE spans(doc INTEGER,first_line INTEGER,last_line INTEGER,body TEXT);
        CREATE TABLE source_status(doc INTEGER,status TEXT);
        INSERT INTO names VALUES ('pump','pump');
        INSERT INTO source_status VALUES (1,'current');
    """)
    raw = b"Unit: C\nOutlet: 11 kPa\nNo inlet measurement was taken."
    path = str(tmp_path / "report.txt")
    sha = hashlib.sha256(raw).hexdigest()
    db.execute("INSERT INTO docs VALUES (1,'pump',?,?,?)", (path, sha, raw))
    db.execute("INSERT INTO spans VALUES (1,1,3,?)", (raw.decode(),))

    def revise(key, changes):
        with db:
            return store.revision(key, key, "chat:fixture:message:1", changes)

    revise(
        "initial",
        [
            dict(
                op="investigate",
                name="trend",
                scope="Unit C",
                question="What explains the reading?",
                discriminator="Check separately",
                theses=["bias", "flow"],
            ),
            dict(op="thesis", name="bias", value="Gauge bias"),
            dict(op="thesis", name="flow", value="Changed flow"),
            dict(
                op="decide",
                name="note",
                value="Unit: C\r\nOutlet: 14 kPa\r\nLabel: retain",
                previous=None,
                scope_fields=["Unit"],
            ),
        ],
    )
    citation = store.cite(1, "Outlet: 11 kPa")
    with db:
        store.revision(
            "report",
            "Outlet: 11 kPa",
            citation,
            [
                dict(
                    op="exhibit",
                    name="trend",
                    kind="specimen",
                    limitation="Unit C only",
                )
            ],
        )
    yield store, revise, citation, tmp_path
    db.close()


def test_replayed_snapshot_rejects_invented_interpretation_and_tampering(investigation):
    store, _, _, home = investigation
    args = dict(project="pump", concept="trend")
    packet, plan = lookup.read(home, args)
    assert lookup.verify(home, args, packet, plan) == [packet["sources"][0]["citation"]]
    packet["sources"][0]["excerpt"] = "The inlet caused the observed drop."
    with pytest.raises(ValueError, match="replay"):
        lookup.verify(home, args, packet, plan)
    with store.db:
        store.db.execute("UPDATE spans SET body='Outlet: 11 kPa' ")
    assert lookup.read(home, args)[0]["unverified"]
    with pytest.raises(ValueError, match="unknown"):
        lookup.read(home, dict(project="other", concept="trend"))


def test_exact_repair_preserves_siblings_and_history(investigation):
    store, revise, citation, _ = investigation
    original = store.resolve("note", 1)
    edit = dict(
        op="amend",
        name="note",
        previous=original["id"],
        old="Outlet: 14 kPa",
        replacement="Outlet: 11 kPa",
        subject="C",
        investigation="trend",
        citation=citation,
        reason="Corrected measured outlet per original bench record.",
    )
    revise("fix", [edit])
    assert (
        store.resolve("note", 1)["value"]
        == "Unit: C\r\nOutlet: 11 kPa\r\nLabel: retain"
    )
    assert store.resolve("note", 1, at=original["event"]) == original
    with pytest.raises(ValueError, match="before replacement"):
        revise("stale", [edit])


@pytest.mark.parametrize(
    "changes",
    [
        {"replacement": "Outlet: 11 kPa proves pump failure"},
        {"replacement": "Inlet: 11 kPa"},
        {"subject": "Unit Q"},
        {"investigation": "different"},
    ],
)
def test_resolving_receipt_cannot_authorize_unrelated_edit(investigation, changes):
    store, revise, citation, _ = investigation
    original = store.resolve("note", 1)
    edit = dict(
        op="amend",
        name="note",
        previous=original["id"],
        old="Outlet: 14 kPa",
        replacement="Outlet: 11 kPa",
        subject="C",
        investigation="trend",
        citation=citation,
        reason="correct one field",
    )
    with pytest.raises(ValueError):
        revise("bad", [edit | changes])
    assert store.resolve("note", 1) == original


def test_rank_checks_by_separable_live_pairs_then_time(investigation):
    store, revise, _, _ = investigation
    option = dict(
        op="candidate_check",
        name="trend",
        key="repeat",
        procedure="Repeat unchanged",
        minutes=1,
        outcomes={"high": "above baseline", "same": "at baseline"},
        predictions={"bias": "high", "flow": "high"},
        inconclusive_if="Noise masks the difference",
    )
    revise("repeat", [option])
    revise(
        "control",
        [
            option
            | dict(
                key="calibrator",
                procedure="Read a reference",
                minutes=5,
                predictions={"bias": "high", "flow": "same"},
            )
        ],
    )
    checks = store.investigation("trend")["candidate_checks"]
    assert [(c["key"], c["separates"]) for c in checks] == [
        ("calibrator", [["bias", "flow"]])
    ]
    revise(
        "retire",
        [dict(op="thesis", name="bias", value="Gauge bias", status="withdrawn")],
    )
    assert not store.investigation("trend")["candidate_checks"]


def test_confident_wrong_reviewer_routes_to_direct_observation():
    result = reviewer.measure(
        [dict(item="A", p_yes=0.99, reason="Apparently clean")],
        [dict(item="A", label=0)],
    )
    assert result["confident_misses"] == 1
    assert result["brier"] == pytest.approx(0.9801)
    assert result["items"][0]["label"] == 0


def test_one_resolving_receipt_can_report_a_different_subject_or_value(investigation):
    _, _, _, home = investigation
    base = {"project": "pump", "concept": "trend"}
    for scope, asked, expected in [
        ({"Unit": "C"}, "11 kPa", "recorded"),
        ({"Unit": "C"}, "14 kPa", "different value recorded"),
        (
            {"Unit": "D"},
            "11 kPa",
            "no matching result recorded in the inspected sources",
        ),
    ]:
        report, paths = lookup.read(
            home, base | {"claim": {"scope": scope, "field": "Outlet", "value": asked}}
        )
        assert report["claim_check"]["status"] == expected
        assert len(paths) == 1


def test_contradictory_measured_outcomes_reopen_the_working_assessment(investigation):
    store, revise, _, home = investigation
    change = dict(
        op="candidate_check",
        name="trend",
        key="ref",
        procedure="Compare the reference gauge",
        minutes=5,
        outcomes={"biased": "reference disagrees", "normal": "reference agrees"},
        predictions={"bias": "biased", "flow": "normal"},
        inconclusive_if="control not matched",
        measurement={
            "field": "Reference",
            "scope": {"Unit": "C", "Mode": "loaded"},
            "values": {"biased": "disagrees", "normal": "agrees"},
        },
    )
    plan = revise("preregister", [change])
    for i, (unit, value) in enumerate(
        [("D", "agrees"), ("C", "agrees"), ("C", "disagrees")], 2
    ):
        lines = f"Unit: {unit}\nMode: loaded\nReference: {value}"
        quote = "Reference: " + value
        raw = lines.encode()
        with store.db:
            store.db.execute(
                "INSERT INTO docs VALUES (?,'pump',?,?,?)",
                (i, str(home / f"r{i}.txt"), hashlib.sha256(raw).hexdigest(), raw),
            )
            store.db.execute("INSERT INTO spans VALUES (?,1,3,?)", (i, lines))
            event = store.revision(
                f"r{i}",
                quote,
                store.cite(i, quote),
                [
                    dict(
                        op="exhibit",
                        name="trend",
                        kind="specimen",
                        limitation="one reference check",
                    ),
                    dict(op="check_result", name="trend", plan=plan, key="ref"),
                ],
            )
        report = store.investigation("trend")
        assert report["check_results"][-1]["prospective"]
        if i == 2:
            assert report["check_results"][-1]["outcome"] is None
            assert not report["working_assessments"]
        elif i == 3:
            assert report["working_assessments"][0]["leader"] == "flow"
            assert not report["candidate_checks"]
            earlier = event
    assert (
        store.investigation("trend", at=earlier)["working_assessments"][0]["leader"]
        == "flow"
    )
    assert report["working_assessments"][0]["status"] == "conflicting results"
    assert "leader" not in report["working_assessments"][0]


def test_near_match_fields_do_not_authorize_patch_when_other_condition_differs():
    receipts = lookup.receipts
    note = "Site: R\nCondition: loaded\nReading: 4"
    source = "Site: R\nCondition: warmup\nReading: 2"
    with pytest.raises(ValueError, match="Condition"):
        receipts.scope_field_match(note, source, "Reading", "Reading: 2", "R", ["Site"])


def test_cited_paragraph_edit_cannot_drop_a_qualifier_or_touch_siblings(investigation):
    store, revise, citation, _ = investigation
    short = citation.partition(":sha256:")[0]
    old = f"The inlet caused the change ({short})."
    prefix, suffix = "First paragraph.\r\n\r\n", "\r\n\r\nRetained limitation."
    revise(
        "letter",
        [dict(op="decide", name="letter", value=prefix + old + suffix, previous=None)],
    )
    original = store.resolve("letter", 1)
    correction = dict(
        op="amend_passage",
        name="letter",
        investigation="trend",
        citation=citation,
        previous=original["id"],
        old=old,
        reason="The cited record only measured outlet pressure.",
    )
    with pytest.raises(ValueError, match="complete"):
        revise("fragment", [correction | {"excerpt": "Outlet: 11 kPa"}])
    with pytest.raises(ValueError):
        revise(
            "partial-old", [correction | {"old": old[4:], "excerpt": "Outlet: 11 kPa"}]
        )
    excerpt = "Unit: C\nOutlet: 11 kPa\nNo inlet measurement was taken."
    revise("repair", [correction | {"excerpt": excerpt}])
    assert (
        store.resolve("letter", 1)["value"]
        == prefix + f"report.txt ({short}) reports: “{excerpt}”" + suffix
    )
    assert store.resolve("letter", 1, at=original["event"]) == original


def test_replacement_source_retracts_only_its_own_prediction(investigation):
    store, revise, _, home = investigation
    options = {"agreed": "agrees", "disagreed": "disagrees"}
    plan = revise(
        "forecast",
        [
            dict(
                op="candidate_check",
                name="trend",
                key="calibrator",
                minutes=3,
                procedure="Measure the reference",
                outcomes=options,
                predictions={"flow": "agreed", "bias": "disagreed"},
                inconclusive_if="Unmatched reference",
                measurement={
                    "field": "Reference",
                    "scope": {"Unit": "C"},
                    "values": options,
                },
            )
        ],
    )

    def exhibit(index, filename, observation):
        raw = ("Unit: C\n" + observation + "\nRun " + str(index)).encode()
        db = store.db
        with db:
            db.execute(
                "UPDATE source_status SET status='superseded' WHERE doc IN (SELECT id FROM docs WHERE path=?)",
                (str(home / filename),),
            )
            db.execute(
                "INSERT INTO docs VALUES (?,'pump',?,?,?)",
                (index, str(home / filename), hashlib.sha256(raw).hexdigest(), raw),
            )
            db.execute("INSERT INTO spans VALUES (?,1,3,?)", (index, raw.decode()))
            db.execute("INSERT INTO source_status VALUES (?,'current')", (index,))
            return store.revision(
                f"activation-{index}",
                observation,
                store.cite(index, observation),
                [
                    dict(
                        op="exhibit",
                        name="trend",
                        kind="specimen",
                        limitation="A single sample.",
                    )
                ],
            )

    first = exhibit(2, "operator-a.txt", "Reference: agrees")
    assert store.investigation("trend")["working_assessments"][0]["leader"] == "flow"
    conflict = exhibit(3, "operator-b.txt", "Reference: disagrees")
    assert (
        store.investigation("trend")["working_assessments"][0]["status"]
        == "conflicting results"
    )
    with store.db:
        draft = store.pause("pump-handoff", 1, "Evidence: {{trend@assessment}}")
    assert "conflicting results" in draft["body"]
    repair = exhibit(4, "operator-b.txt", "Reference: not recorded")
    with store.db:
        job = store.resume("pump-handoff")
    assert job["readback"]["matches_governing_render"]
    assert job["saved"]["body"] == Path(job["path"]).read_text()
    assert (
        "Changed flow" in job["saved"]["body"]
        and "conflicting results" not in job["saved"]["body"]
    )
    current = store.investigation("trend")
    assert current["working_assessments"][0]["leader"] == "flow"
    assert [r.get("superseded_by") for r in current["check_results"]] == [
        None,
        repair,
        None,
    ]
    assert current["check_results"][-1]["outcome"] is None
    assert (
        store.investigation("trend", at=conflict)["working_assessments"][0]["status"]
        == "conflicting results"
    )
    assert (
        store.investigation("trend", at=first)["working_assessments"][0]["leader"]
        == "flow"
    )
    with store.db:
        store.db.execute("UPDATE source_status SET status='superseded' WHERE doc=2")
    assert not store.investigation("trend")["working_assessments"]
    assert store.investigation("trend")["candidate_checks"][0]["revision"] == plan


def test_measured_disagreement_within_one_original_cannot_be_reported_as_absence():
    source = {
        "coverage": "whole section",
        "citation": "record:1",
        "source_status": "current",
        "context": "Wing: S\nTally: 13\n\nWing: S\nTally: 9\n\nWing: N\nTally: 22",
    }
    query = {"scope": {"Wing": "S"}, "field": "Tally"}
    check = lookup.receipts.claim_support([source], query)
    assert check["status"] == "conflicting recorded values"
    assert [row["value"] for row in check["measurements"]] == ["13", "9"]
    assert (
        lookup.receipts.scoped_value(source["context"], query["scope"], "Tally")[0]
        is None
    )


def test_bounded_read_includes_inspected_nonmatches_in_the_frozen_plan(investigation):
    store, _, _, home = investigation
    for i in range(2, 8):
        raw = f"Unit: R{i}\nOutlet: {i} kPa".encode()
        with store.db:
            store.db.execute(
                "INSERT INTO docs VALUES (?,'pump',?,?,?)",
                (i, str(home / f"r{i}.txt"), hashlib.sha256(raw).hexdigest(), raw),
            )
            store.db.execute("INSERT INTO spans VALUES (?,1,2,?)", (i, raw.decode()))
            store.revision(
                f"other-{i}",
                f"Outlet: {i} kPa",
                store.cite(i, f"Outlet: {i} kPa"),
                [
                    dict(
                        op="exhibit",
                        name="trend",
                        kind="specimen",
                        limitation="One named unit",
                    )
                ],
            )
    q = {
        "project": "pump",
        "concept": "trend",
        "claim": {"scope": {"Unit": "C"}, "field": "Outlet"},
    }
    packet, originals = lookup.read(home, q)
    assert packet["claim_check"]["measurements"][0]["value"] == "11 kPa"
    assert len(packet["sources"]) == 5
    assert len(lookup.verify(home, q, packet, originals)) == 7
    del originals[str(home / "r2.txt")]
    with pytest.raises(lookup.OutsidePlan):
        lookup.verify(home, q, packet, originals)


def test_tool_description_allows_empty_schema_defaults_but_keeps_constraints():
    import copy

    actual = copy.deepcopy(lookup.description())
    scope = actual["tools"][lookup.TOOL]["parameters"]["properties"]["claim"][
        "properties"
    ]["scope"]
    scope.update(required=[], properties={})
    assert lookup.description_digest(actual) == lookup.description_digest(
        lookup.description()
    )
    scope["description"] = "a different statement"
    assert lookup.description_digest(actual) != lookup.description_digest(
        lookup.description()
    )


def test_checks_that_jointly_rule_out_known_options_are_not_conflicting_reports():
    alternatives = [{"name": k, "status": "open"} for k in ("x", "y")]
    result = dict(
        revision=3,
        plan=1,
        key="first",
        scope={"Bay": "A"},
        predictions={"x": "yes", "y": "no"},
        outcome="yes",
        prospective=True,
        citation="first",
    )
    second = result | dict(plan=2, key="second", citation="second", outcome="no")
    assessment = lookup.checks.assess(alternatives, [result, second])[0]
    assert (
        assessment["status"]
        == "none of the registered alternatives fits all measured checks"
    )
    assert "leader" not in assessment


def test_label_matching_preserves_identifiers_units_and_ambiguous_columns():
    r = lookup.receipts
    src = {
        "coverage": "whole section",
        "citation": "original",
        "context": "Desk: Elm\nBatch: L7\nPower mW: 3\nPower MW: 19",
    }
    q = {"scope": {"desk": "Elm", "batch": "L7"}, "field": "power mW"}
    assert r.claim_support([src], q, normalized=True)["measurements"][0]["value"] == "3"
    assert (
        r.claim_support([src], q | {"field": "power MW"}, normalized=True)[
            "measurements"
        ][0]["value"]
        == "19"
    )
    assert not r.claim_support(
        [src], q | {"scope": {"desk": "elm", "batch": "L7"}}, normalized=True
    )["measurements"]
    colliding = src | {"context": src["context"] + "\ndesk: other"}
    assert (
        r.claim_support([colliding], q, normalized=True)["status"]
        == "ambiguous source field labels"
    )


def test_archive_receipt_authorizes_only_that_project_record(investigation):
    _, _, citation, home = investigation
    args = {"project": "pump", "concept": "trend"}
    packet, paths = lookup.read(home, args)
    archived = lookup.freeze_receipts(home, ["pump", "trend"], [citation])
    assert lookup.verify(home, args, packet, {}, archived) == [citation]
    with pytest.raises(lookup.OutsidePlan):
        lookup.verify(home, args, packet, {}, {})
    with pytest.raises(ValueError, match="absent"):
        lookup.freeze_receipts(home, ["pump", "different"], [citation])
    with pytest.raises(ValueError, match="distinct"):
        lookup.freeze_receipts(home, ["pump", "trend"], [citation, citation])
