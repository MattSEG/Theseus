import importlib.util
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

script = (
    Path(__file__).resolve().parents[2]
    / "skills/research/revisable-memory/scripts/memory.py"
)
spec = importlib.util.spec_from_file_location("revisable_memory", script)
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


@pytest.fixture
def ledger(tmp_path):
    m = memory.Memory(tmp_path, "test")
    yield m
    m.db.close()


def apply(m, key, *changes):
    with m.db:
        m.db.execute("BEGIN IMMEDIATE")
        return m.revision(key, "evidence " + key, "chat:test:" + key, list(changes))


def decide(name, value, previous=None, **options):
    return dict(op="decide", name=name, value=value, previous=previous, **options)


def branch(name, parent):
    return dict(op="branch", name=name, parent=parent)


def resume(m, name):
    with m.db:
        m.db.execute("BEGIN IMMEDIATE")
        return m.resume(name)


def test_late_split_and_immutable_file(ledger):
    m = ledger
    original = apply(m, "original", decide("wrap", "Paper"))
    base = m.governing("wrap", 4)["id"]
    with m.db:
        m.pause("parcel", 4, "{{wrap:shop}} / {{wrap:mail}}")
        m.pause("generic", 4, "{{wrap}}")
    apply(m, "pivot", branch("mail", "wrap"), decide("mail", "Box", base, edition=4))
    assert m.governing("mail", 3)["id"] == base
    assert m.governing("wrap", 4, at=original)["id"] == base
    assert m.governing("wrap", 4) is None
    with pytest.raises(ValueError, match="job remains paused"):
        resume(m, "generic")
    result = resume(m, "parcel")
    path = Path(result["path"])
    assert result["draft"]["body"] == "Paper / Paper"
    assert path.read_text() == "Paper / Box"
    stamp = path.stat().st_mtime_ns
    apply(m, "later", decide("mail", "Crate", m.resolve("mail", 4)["id"], edition=4))
    assert resume(m, "parcel") == result
    assert path.stat().st_mtime_ns == stamp
    path.write_text("external edit")
    with pytest.raises(ValueError, match="changed"):
        resume(m, "parcel")
    assert path.read_text() == "external edit"


def test_dependent_claim_and_independent_support(ledger):
    m = ledger
    apply(m, "premises", decide("stock", "Matte"), decide("vendor", "Approved"))
    stock = {"concept": "stock", "id": m.resolve("stock", 1)["id"]}
    vendor = {"concept": "vendor", "id": m.resolve("vendor", 1)["id"]}
    apply(
        m,
        "conclusions",
        decide("proof", "Use matte", supports=[[stock]]),
        decide("order", "Place order", supports=[[stock], [vendor]]),
    )
    with m.db:
        m.pause("preflight", 2, "{{proof}}")
    apply(m, "switch", decide("stock", "Gloss", stock["id"], edition=2))
    assert m.resolve("proof", 1)
    assert m.resolve("proof", 2) is None
    assert m.resolve("order", 2)
    with pytest.raises(ValueError, match="remains paused"):
        resume(m, "preflight")


def test_theses_do_not_govern(ledger):
    m = ledger

    def thesis(name, value, status):
        return dict(op="thesis", name=name, value=value, status=status)

    apply(
        m,
        "ideas",
        thesis("stock", "Surface changed", "open"),
        thesis("press", "Ink drift", "open"),
    )
    apply(m, "sample", thesis("press", "Ink measurements stable", "rejected"))
    assert not m.resolve("press", 1) and not m.resolve("stock", 1)
    assert [
        r["status"]
        for r in m.rows(
            "SELECT status FROM memory_theses WHERE project=? ORDER BY event,name"
        )
    ] == ["open", "open", "rejected"]


def test_rollback_retry_scope_and_isolation(ledger, tmp_path):
    m = ledger
    first = apply(m, "first", decide("rule", "General"))
    assert apply(m, "first", decide("rule", "General")) == first
    claim = m.resolve("rule", 1)["id"]
    with pytest.raises(ValueError):
        apply(
            m,
            "invalid",
            branch("part", "rule"),
            decide("rule", "overwrite", previous=999),
        )
    assert not m.parent("part", 999)
    apply(m, "exception", decide("rule", "Rush", claim, job="rush"))
    assert m.governing("rule", 1, "rush")["value"] == "Rush"
    assert m.governing("rule", 1, "normal")["value"] == "General"
    assert memory.Memory(tmp_path, "other").resolve("rule", 1) is None


def test_concurrent_resumes_and_lost_ack(ledger, tmp_path):
    m = ledger
    apply(m, "base", decide("label", "Green"))
    with m.db:
        m.pause("label-job", 1, "{{label}}")

    def retry(_):
        other = memory.Memory(tmp_path, "test")
        try:
            return resume(other, "label-job")
        finally:
            other.db.close()

    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(retry, range(2)))
    assert a == b
    file = Path(a["path"])
    stamp = file.stat().st_mtime_ns
    with m.db:
        m.db.execute("UPDATE memory_jobs SET done=0")
    apply(m, "later", decide("label", "Red", m.resolve("label", 1)["id"]))
    assert resume(m, "label-job") == a
    assert file.stat().st_mtime_ns == stamp


def test_citation_bound_to_speaking_chat(tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute("CREATE TABLE messages(id,session_id,role,content)")
        db.executemany(
            "INSERT INTO messages VALUES(?,?,?,?)",
            [(1, "a", "user", "old decision"), (2, "b", "user", "new decision")],
        )
    monkeypatch.setenv("HERMES_SESSION_ID", "b")
    assert memory.chat_source(tmp_path, "new decision") == "chat:b:message:2"
    with pytest.raises(ValueError):
        memory.chat_source(tmp_path, "old decision")


def test_evidence_can_support_one_thesis_and_weaken_another(ledger):
    m = ledger
    apply(
        m,
        "theses",
        dict(op="thesis", name="stock", value="Paper", status="open"),
        dict(op="thesis", name="press", value="Calibration", status="open"),
    )
    changes = [
        dict(op="observe", name="stock", direction="against"),
        dict(op="observe", name="press", direction="for"),
    ]
    with m.db:
        m.revision("sample", "Sample across stock", "record:13:snapshot:1", changes)
    assert len(m.rows("SELECT * FROM memory_observations WHERE project=?")) == 2
    assert not m.governing("press", 1)
    with pytest.raises(ValueError, match="chat decision"):
        with m.db:
            m.revision(
                "reimport",
                "An older guide",
                "record:14:snapshot:2",
                [decide("print", "Old rule")],
            )
    assert not m.governing("print", 1)


def test_old_document_reimport_does_not_reinstate_a_decision(
    ledger, tmp_path, monkeypatch
):
    import os
    import subprocess
    import sys

    m = ledger
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    guide = tmp_path / "guide.md"
    old_text = "Digital proofs are sufficient."
    guide.write_text("# Original guide\n" + old_text + "\n")
    root = script.parents[4]
    importer = root / "skills/research/project-recall/scripts/recall.py"

    def remember(project):
        subprocess.run(
            [sys.executable, str(importer), "remember", project, str(guide)],
            check=True,
            capture_output=True,
            env={**os.environ, "PYTHONPATH": str(root)},
        )

    remember("test")
    record = m.db.execute(
        "SELECT rowid FROM spans WHERE body LIKE '%Digital%'"
    ).fetchone()[0]
    citation = m.cite(record, old_text)
    apply(m, "before", decide("print", "Digital"))
    apply(m, "correction", decide("print", "Physical", m.resolve("print", 1)["id"]))
    remember("test")
    assert m.cite(record, old_text) == citation
    assert m.resolve("print", 1)["value"] == "Physical"
    with pytest.raises(ValueError):
        memory.Memory(tmp_path, "elsewhere").cite(record, old_text)


def test_investigation_exhibit_cannot_promote_old_claim(ledger):
    m = ledger
    initial = apply(
        m,
        "initial",
        decide("bench", "Earlier"),
        dict(
            op="investigate",
            name="bench",
            question="What happened?",
            scope="First specimen",
            discriminator="Locate its bench trace.",
        ),
    )
    with m.db:
        m.pause("report", 1, "{{bench}}")
    apply(m, "correct", decide("bench", "Revised", m.resolve("bench", 1)["id"]))
    with m.db:
        m.revision(
            "paper",
            "Other specimens",
            "record:1:snapshot:1:sha256:example",
            [
                dict(
                    op="exhibit",
                    name="bench",
                    kind="background",
                    limitation="Other material",
                )
            ],
        )
    now = m.investigation("bench")
    assert now["current"]["value"] == "Revised"
    assert "Locate" in now["next_review"]
    assert not m.investigation("bench", at=initial)["evidence"]
    assert resume(m, "report")["saved"]["body"] == "Revised"
    assert "Verified saved file" in m.investigation_page(now, "report")


def test_research_review_uses_current_basis(ledger):
    m = ledger
    apply(
        m,
        "start",
        decide("cause", "First"),
        dict(
            op="investigate",
            name="cause",
            question="Which cause fits?",
            scope="Original specimen",
            discriminator="Check source.",
        ),
    )
    old = m.resolve("cause", 1)["id"]
    apply(m, "pivot", decide("cause", "Second", old))
    review = dict(
        op="review",
        name="cause",
        basis=old,
        move="redirect",
        reason="Scope changed",
        next_check="Find a matching observation.",
    )
    with pytest.raises(ValueError, match="outdated"):
        apply(m, "review", review)
    review["basis"] = m.resolve("cause", 1)["id"]
    review["basis_revision"] = m.investigation("cause")["revision"]
    apply(m, "review", review)
    assert m.investigation("cause")["reviews"][0]["move"] == "redirect"


def test_method_history_preserves_failed_outcome_and_subquestion(ledger):
    m = ledger
    q = dict(
        op="investigate",
        name="root",
        question="What changed?",
        scope="Run one",
        discriminator="Check log.",
    )
    apply(m, "start", q)
    apply(m, "child", dict(q, name="trace", parent="root"))
    review = apply(
        m,
        "try",
        dict(
            op="review",
            name="trace",
            basis=None,
            basis_revision=m.investigation("trace")["revision"],
            move="continue",
            reason="Check the log",
            next_check="Look for the timestamp",
            method="time-align",
        ),
    )
    apply(
        m,
        "result",
        dict(
            op="outcome",
            name="trace",
            review=review,
            result="failed",
            conditions="Clock missing",
        ),
    )
    assert m.investigation("trace")["parent"] == "root"
    assert m.method_history("time-align")[0]["conditions"] == "Clock missing"


def test_pause_time_and_live_claims_survive_two_corrections(ledger):
    apply(ledger, "initial", decide("reading", "first"))
    with ledger.db:
        draft = ledger.pause("note", 1, "{{reading@pause}} / {{reading}}")
    original = draft["claims"]["reading"]
    apply(ledger, "update", decide("reading", "second", original))
    apply(ledger, "latest", decide("reading", "third", ledger.resolve("reading", 1)["id"]))
    with ledger.db:
        saved = ledger.resume("note")
        retry = ledger.resume("note")
    assert Path(saved["path"]).read_text() == "first / third"
    assert saved["saved"]["claims"]["reading@pause"] == original
    assert retry["sha256"] == saved["sha256"]


def test_impact_uses_revision_scope_and_preserves_old_job_state(ledger):
    m = ledger
    apply(m, "source", decide("lead", "four weeks"), decide("price", "$30"))
    claim = m.resolve("lead", 1)["id"]
    apply(m, "plan", decide("launch", "May launch", supports=[[{"concept":"lead","id":claim}]]))
    with m.db:
        m.pause("quote", 1, "Cost {{price}}.\nTiming: {{lead}}.\nPlan: {{launch}}.\nFormer: {{lead@pause}}.")
        m.pause("old-copy", 1, "Timing: {{lead}}.")
    published = Path(resume(m, "old-copy")["path"])
    e = apply(m, "delay", decide("lead", "six weeks", claim))
    report = m.impact(e)
    jobs = {job["job"]:job for job in report["jobs"]}
    assert jobs["old-copy"]["state_at_revision"] == "published"
    assert [line["line"] for line in jobs["quote"]["lines"]] == [2,3]
    assert jobs["quote"]["lines"][0]["before"]["body"] == "Timing: four weeks."
    assert jobs["quote"]["lines"][0]["after"]["body"] == "Timing: six weeks."
    assert "pending" in jobs["quote"]["lines"][1]["after"]
    assert published.read_text() == "Timing: four weeks."
    apply(m,"another",decide("lead","two weeks",m.resolve("lead",1)["id"]))
    with m.db:
        m.pause("later", 1, "{{lead}}")
    assert m.impact(e) == report
    with pytest.raises(ValueError,match="not in this project"):
        m.impact(e+100)


def test_impact_accepts_independent_support_and_flags_legacy_coverage(ledger):
    m = ledger
    apply(m, "inputs", decide("first", "yes"), decide("second", "yes"))
    groups = [[dict(concept=name,id=m.resolve(name,1)["id"])] for name in ("first","second")]
    apply(m,"choice",decide("plan","Ship",supports=groups))
    with m.db:
        m.pause("handoff",1,"{{plan}}")
    event = apply(m,"correct",decide("first","no",m.resolve("first",1)["id"]))
    report=m.impact(event)
    assert report["jobs"] == []
    assert {c["concept"] for c in report["claims"]} == {"first"}
    assert all(c["after"]["citation"] == "chat:test:correct" for c in report["claims"])
    with m.db:
        m.db.execute("DELETE FROM memory_impact_inventory WHERE event=?",(event,))
    assert m.impact(event)["jobs_at_revision"] is None
