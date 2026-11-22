import importlib.util
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "skills/software-development/learning-run/scripts/proof.py"
spec = importlib.util.spec_from_file_location("learning_proof", SOURCE)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


@pytest.fixture(autouse=True)
def isolated_artifact_roots(tmp_path,monkeypatch):
    # The artifact hard-link guard compares file identities under each profile root.
    monkeypatch.setenv("HOME",str(tmp_path))
    monkeypatch.setenv("CODEX_HOME",str(tmp_path/".codex"))


def test_extracts_static_evidence(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<head><title>Head</title><style>bad</style></head><h1>Saved</h1>"
                    "<div hidden>invisible<span>nested</span></div><!-- approval -->"
                    "<script>never execute</script><p>Artifact &amp; source</p>")
    assert proof.artifact_text(page)[0] == "Saved Artifact & source"


def test_requires_verbatim_evidence():
    item = {"status": "pass", "requirement": "Deliver heading", "reason": "Heading present",
            "evidence": "Checking output"}
    assert proof.validate({"checks": [item]}, "Checking output") == "pass"
    with pytest.raises(ValueError, match="does not match"):
        proof.validate({"checks": [item]}, "Different heading")
    item.update(status="fail", evidence="")
    assert proof.validate({"checks": [item]}, "Different heading") == "fail"


def test_receipt_detects_artifact_request_and_reference_changes(tmp_path, monkeypatch, capsys):
    import json

    request, artifact, ref = [tmp_path / name for name in ["request.txt", "saved.txt", "guide.md"]]
    for p in (request, artifact, ref):
        p.write_text("Original text")
    def review(*args):
        return {"checks": [{"status": "pass", "requirement": "Text", "reason": "Present",
                            "evidence": "Original text"}]}, "new-session"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(proof, "fresh_review", review)
    monkeypatch.setattr("sys.argv", ["proof", "review", str(request), str(artifact), "--reference", str(ref)])
    assert proof.main() == 0
    receipt = next((tmp_path / "proofs").glob("*.json"))
    monkeypatch.setattr("sys.argv", ["proof", "check", str(receipt)])
    assert proof.main() == 0
    for path in (request, artifact, ref):
        path.write_text("Changed")
        assert proof.main() == 2
        state = json.loads(capsys.readouterr().out.splitlines()[-1])
        assert state == {"evidence": "changed", "review": None, "recorded_review": "pass"}
        path.write_text("Original text")
    assert proof.main() == 0


def test_acceptance_checklist_detects_omission_and_duplicate():
    request = "Notes\n- a prior example\n## Acceptance criteria\n- [ ] State the title\n2. Include\n   the edition\n## Examples\n- a different edition"
    criteria = proof.acceptance_criteria(request)
    assert criteria == ["State the title", "Include the edition"]
    title = dict(requirement=criteria[0], status="pass", evidence="Review", reason="Title visible.")
    edition = dict(requirement=criteria[1], status="fail", evidence="", reason="Edition absent.")
    for incomplete in [[title], [title, title, edition]]:
        with pytest.raises(ValueError, match="checklist"):
            proof.validate({"checks": incomplete}, "Review", criteria)
    assert proof.validate({"checks": [edition, title]}, "Review", criteria) == "fail"


def test_receipt_check_rechecks_rendered_text(tmp_path, monkeypatch, capsys):
    request, artifact = tmp_path / "request.txt", tmp_path / "print.html"
    request.write_text("Include the edition.")
    artifact.write_text("<p>Edition 6</p>")
    digest = proof.artifact_text(artifact)[1]
    rendering = {"media": "print", "viewport": {"width": 1280, "height": 900},
                 "source_sha256": digest, "text_sha256": "testlayout"}
    monkeypatch.setattr(proof, "render_evidence", lambda *_: ("Edition 6", digest, rendering.copy()))
    monkeypatch.setattr(proof, "fresh_review", lambda *_: ({"checks": [
        dict(requirement="Edition", status="pass", evidence="Edition 6", reason="Found.")]}, "fresh"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["proof", "review", str(request), str(artifact), "--render", "print"])
    assert proof.main() == 0
    receipt = next((tmp_path / "proofs").glob("*.json"))
    monkeypatch.setattr("sys.argv", ["proof", "check", str(receipt)])
    assert proof.main() == 0
    rendering["text_sha256"] = "changedlayout"
    assert proof.main() == 2


def test_proof_refuses_dummy_login_file_before_reviewer(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    codex = tmp_path / ".codex"
    codex.mkdir()
    auth = codex / "auth.json"
    auth.write_text("dummy placeholder")
    renamed = tmp_path / "question.txt"
    os.link(auth, renamed)
    artifact = tmp_path / "approval.txt"
    artifact.write_text("Review owner")
    monkeypatch.setattr("sys.argv", ["proof", "review", str(renamed), str(artifact)])
    monkeypatch.setattr(proof, "fresh_review", lambda *_: pytest.fail("reviewer must not be invoked"))
    with pytest.raises(ValueError, match="authentication stores"):
        proof.main()
    assert not (tmp_path / "profile/proofs").exists()


def test_crash_observations_withhold_scorer_conclusions(tmp_path):
    import json

    path = tmp_path / "receipt.json"
    row = dict(case="slow", saved_ready={"5.0": 0, "6.0": 1}, earlier_saves_omitted=0,
               saves=[{"number": 1, "edition": "6.0", "duplicate": False}], passed=False,
               expected_ready={"fake": True}, untrusted_extra="discard this")
    path.write_text(json.dumps({"results": [row], "verified": False}))
    text, _ = proof.artifact_text(path)
    assert "Edition 6.0 is ready for print." in text and "Edition 5.0 is draft." in text
    assert "discard this" not in text and "fake" not in text and "verified" not in text
    del row["saves"]
    path.write_text(json.dumps({"results": [row]}))
    with pytest.raises(ValueError, match="--details"):
        proof.artifact_text(path)


def test_reviews_persisted_state_and_invalidates_receipt_on_later_save(tmp_path, monkeypatch, capsys):
    import json
    import sqlite3
    import sys

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    trials = tmp_path / "desktop-trials"
    trials.mkdir()
    database = trials / "print.sqlite"
    with sqlite3.connect(database) as db:
        db.executescript("""CREATE TABLE cards(scenario,edition,ready);
            CREATE TABLE actions(scenario,edition,duplicate);
            INSERT INTO cards VALUES('slow','5.0',0),('slow','6.0',1);
            INSERT INTO actions VALUES('slow','6.0',0);""")
    artifact = tmp_path / "score.json"
    row = dict(case="slow", saved_ready={"6.0": 1, "5.0": 0}, earlier_saves_omitted=0,
               saves=[dict(number=1, edition="6.0", duplicate=False)])
    record = dict(trial="print", results=[row], verified=False)
    artifact.write_text(json.dumps(record))
    req = tmp_path / "request.md"
    req.write_text("Set edition 6.0 ready.")
    def reviewer(request, text, refs, launcher, criteria, trial_readback):
        assert trial_readback and "edition 5.0 has 0 recorded saves." in text
        return {"checks": [dict(requirement="Ready", status="pass", evidence="Edition 6.0 is ready for print.",
                                reason="Stored") ]}, "fixture"
    monkeypatch.setattr(proof, "fresh_review", reviewer)
    monkeypatch.setattr(sys, "argv", ["proof", "review", str(req), str(artifact)])
    assert proof.main() == 0
    receipt = json.loads(capsys.readouterr().out)["receipt"]
    monkeypatch.setattr(sys, "argv", ["proof", "check", receipt])
    assert proof.main() == 0
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO actions VALUES('slow','6.0',1)")
    assert proof.main() == 2
    with pytest.raises(ValueError, match="differ"):
        proof.readback_trial(artifact)


@pytest.mark.parametrize("alter", ["summary", "quote", "omitted", "request", "criteria"])
def test_offline_replay_rejects_inconsistent_checklist(tmp_path, monkeypatch, capsys, alter):
    import json

    request = tmp_path / "request.md"
    request.write_text("Acceptance criteria\n- Include the edition.\n- Include the date.")
    artifact = tmp_path / "proof.txt"
    artifact.write_text("Edition 6.")
    criteria = proof.acceptance_criteria(request.read_text())
    checks = [
        dict(requirement=criteria[0], status="pass", evidence="Edition 6.", reason="Present."),
        dict(requirement=criteria[1], status="fail", evidence="", reason="Missing date."),
    ]
    monkeypatch.setattr(proof, "fresh_review", lambda *_: ({"checks": checks}, "test"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["proof", "review", str(request), str(artifact)])
    assert proof.main() == 0
    receipt = Path(json.loads(capsys.readouterr().out)["receipt"])
    monkeypatch.setattr("sys.argv", ["proof", "check", str(receipt)])
    assert proof.main() == 0
    assert json.loads(capsys.readouterr().out)["review"] == "fail"
    saved = json.loads(receipt.read_text())
    if alter == "summary":
        saved["verdict"] = "pass"
    elif alter == "quote":
        saved["checks"][0]["evidence"] = "Edition 7."
    elif alter == "omitted":
        saved["checks"].pop()
        saved["verdict"] = "pass"
    elif alter == "request":
        saved["request"] = "Publish it."
    else:
        saved["acceptance_criteria"] = []
    receipt.write_text(json.dumps(saved))
    assert proof.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "evidence": "current", "receipt": "inconsistent", "review": None}


def test_proof_delivery_binds_acknowledgement_destination_and_bytes(tmp_path, monkeypatch, capsys):
    import hashlib
    import json
    import sqlite3

    monkeypatch.syspath_prepend(str(SOURCE.parent))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    request = tmp_path / "request.md"
    request.write_text("Confirm the acknowledged local delivery.")
    note = tmp_path / "saved.md"
    note.write_text("Ready for print.")
    digest = hashlib.sha256(note.read_bytes()).hexdigest()
    (tmp_path / "deliveries").mkdir()
    db = sqlite3.connect(tmp_path / "deliveries/jobs.sqlite")
    db.execute("CREATE TABLE deliveries(key,destination,sha,done)")
    db.execute("INSERT INTO deliveries VALUES(?,?,?,1)", ("proof-key", str(note), digest))
    db.commit()

    def review(*args, delivery_evidence):
        assert str(note) in delivery_evidence and digest in delivery_evidence
        return {"checks": [dict(requirement="Confirm local delivery", status="pass",
                                evidence="Local delivery job proof-key is acknowledged.", reason="Recorded")]}, "fixture"

    monkeypatch.setattr(proof, "fresh_review", review)
    monkeypatch.setattr("sys.argv", ["proof", "review", str(request), str(note), "--delivery", "proof-key"])
    assert proof.main() == 0
    receipt = Path(json.loads(capsys.readouterr().out)["receipt"])
    monkeypatch.setattr("sys.argv", ["proof", "check", str(receipt)])
    assert proof.main() == 0
    db.execute("UPDATE deliveries SET done=0")
    db.commit()
    assert proof.main() == 2
    db.execute("UPDATE deliveries SET done=1")
    db.commit()
    other = tmp_path / "same-bytes.md"
    other.write_bytes(note.read_bytes())
    with pytest.raises(ValueError, match="destination and bytes"):
        proof.verified_delivery("proof-key", other, digest)
    db.close()


def test_verbatim_text_evidence_retains_line_structure_and_legacy_projection(tmp_path):
    card=tmp_path/"card.csv"
    raw=b"Item,Count\r\nBlue,4\r\n\r\n  Spare,2\r\n"
    card.write_bytes(raw)
    text,digest=proof.artifact_text(card)
    assert text.encode()==raw
    legacy,old_digest=proof.artifact_text(card,preserve_lines=False)
    assert legacy=="Item,Count Blue,4 Spare,2" and old_digest==digest
    check=dict(status="pass",requirement="One CSV row per item",reason="Separate lines",evidence="Item,Count\r\nBlue,4")
    assert proof.validate({"checks":[check]},text,verbatim=True)=="pass"
    with pytest.raises(ValueError,match="does not match"):
        proof.validate({"checks":[check|{"evidence":"Item,Count Blue,4"}]},text,verbatim=True)


def test_source_process_needs_independent_observation_while_content_can_pass():
    assert proof.source_process_requested("Inspect the measured field using a scoped query.")
    assert proof.source_process_requested("Use only the native investigation_lookup tool.")
    assert not proof.source_process_requested("Report the measured field and cite its original source.")
    assert proof.combined_verdict("pass",{"status":"unverified"})=="unverified"
    assert proof.combined_verdict("pass",{"status":"fail"})=="fail"
    assert proof.combined_verdict("fail",{"status":"pass"})=="fail"


def test_packaged_reviewer_requires_theseus_identity(tmp_path,monkeypatch):
    import hermes_cli
    import plistlib
    app=tmp_path/"Theseus.app/Contents"
    resources=app/"Resources"
    package=resources/"backend/hermes_cli/__init__.py"
    python=resources/"python/bin/python3"
    for path in (package,python):
        path.parent.mkdir(parents=True);path.touch()
    monkeypatch.setattr(hermes_cli,"__file__",str(package))
    missing=tmp_path/"absent-launcher"
    (app/"Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier":"local.theseus.desktop"}))
    assert proof.reviewer_command(missing)==[str(python),"-I","-B","-m","hermes_cli.main"]
    (app/"Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier":"example.other.desktop"}))
    with pytest.raises(ValueError,match="packaged Desktop interpreter"):
        proof.reviewer_command(missing)
