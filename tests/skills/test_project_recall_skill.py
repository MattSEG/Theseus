import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[2] / "skills/research/project-recall/scripts/recall.py"


def test_project_recall_versions_and_aliases(tmp_path):
    def run(*args):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, check=True,
                                text=True, env={**os.environ, "HERMES_HOME": str(tmp_path)})
        return [json.loads(line) for line in result.stdout.splitlines()]

    a, b = tmp_path / "guide.md", tmp_path / "notes.md"
    original = b"# Palette\r\nGraphite, revision 1.\r\n"
    a.write_bytes(original)
    b.write_text("# Samples\nPaper samples.")
    first = run("remember", "Studio", str(a))
    assert first == run("remember", "Studio", str(a))
    run("remember", "ST", str(b))
    assert not run("recall", "ST", "Graphite")
    old = run("recall", "Studio", "Graphite")[0]
    run("merge-name", "Studio", "ST")
    assert run("recall", "ST", "Graphite") == [old]
    assert run("recall", "Studio", "samples")[0]["source"] == str(b)
    a.write_text("# Palette\nSilver, revision 2.")
    run("remember", "Studio", str(a))
    assert not run("recall", "ST", "Graphite")
    assert run("recall", "ST", "Silver")[0]["record"] != old["record"]
    assert run("quote", str(old["record"]))[0]["text"].encode() == original
    with sqlite3.connect(tmp_path / "project-recall/records.sqlite") as db:
        assert db.execute("SELECT data FROM docs WHERE id=?", (first[0]["snapshot"],)).fetchone()[0] == original


def test_dream_does_not_rewrite_source_records(tmp_path):
    def call(*args):
        return subprocess.check_output([sys.executable, str(SCRIPT), *args], text=True,
                                       env={**os.environ, "HERMES_HOME": str(tmp_path)})

    for name, text in [("ARC Engineering", "Typography"), ("A.R.C. Engineering", "Palette"),
                       ("ARC Engineering East", "Unrelated")]:
        path = tmp_path / (name + ".md")
        path.write_text(text)
        call("remember", name, str(path))
    db = sqlite3.connect(tmp_path / "project-recall/records.sqlite")
    def snapshot():
        return db.execute("SELECT * FROM docs ORDER BY id").fetchall()
    before = snapshot()
    assert "ARC".lower() in call("dream")
    assert not call("recall", "ARC Engineering", "Palette")
    call("dream", "--apply")
    assert "Palette" in call("recall", "ARC Engineering", "Palette")
    assert not call("recall", "ARC Engineering", "Unrelated")
    assert not call("dream", "--apply")
    assert before == snapshot()


def test_reactivation_and_migration_keep_historical_citations(tmp_path):
    def call(*args):
        result = subprocess.check_output([sys.executable, str(SCRIPT), *args], text=True,
                                         env={**os.environ, "HERMES_HOME": str(tmp_path)})
        return [json.loads(line) for line in result.splitlines()]

    path = tmp_path / "edition.txt"
    for content in ["Draft", "Approved", "Draft", "Draft"]:
        path.write_text(content)
        call("remember", "Print", str(path))
    assert [r["snapshot"] for r in call("timeline", "Print", str(path))] == [1, 2, 1]
    assert call("quote", "2")[0]["status"] == "superseded"
    assert call("quote", "1")[0]["status"] == "current"
    with sqlite3.connect(tmp_path / "project-recall/records.sqlite") as db:
        db.execute("DROP TABLE activations")
    call("merge-name", "Print", "Print Studio")
    migrated = call("timeline", "Print Studio", str(path))
    assert len(migrated) == 1 and migrated[0]["origin"] == "existing-current"
    assert migrated[0]["observed"] is None
    assert call("quote", "2")[0]["text"] == "Approved"


def test_procedure_requires_current_evidence_for_each_step(tmp_path):
    def call(*args, success=True):
        p = subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True,
                           env={**os.environ, "HERMES_HOME": str(tmp_path)})
        assert (p.returncode == 0) == success, p.stderr
        return [json.loads(line) for line in p.stdout.splitlines()]

    a, b = tmp_path / "proof.md", tmp_path / "release.md"
    a.write_text("Check the trim.")
    b.write_text("Verify the title.")
    call("remember", "Print", str(a))
    call("remember", "Print", str(b))
    args = ["Print", "Proof review", "--step", "1", "Check the trim.", "--step", "2", "Verify the title."]
    assert call("compose", *args) == call("compose", *args)
    call("compose", "Other", *args[1:], success=False)
    call("compose", *args[:-1], "Different text", success=False)
    assert [s["record"] for s in call("procedures", "Print", "proof")[0]["steps"]] == [1, 2]
    b.write_text("Verify the footer.")
    call("remember", "Print", str(b))
    assert call("procedures", "Print", "proof") == []
    stale = call("procedures", "Print", "proof", "--include-stale")[0]
    assert stale["status"] == "stale" and stale["steps"][1]["status"] == "superseded"
    call("compose", *args, success=False)


def test_alias_conflict_requires_explicit_reread(tmp_path):
    def call(*args):
        output = subprocess.check_output([sys.executable, str(SCRIPT), *args], text=True,
                                         env={**os.environ, "HERMES_HOME": str(tmp_path)})
        return [json.loads(line) for line in output.splitlines()]
    guide = tmp_path / "guide.md"
    guide.write_text("Proof draft")
    call("remember", "Brand Studio", str(guide))
    call("compose", "Brand Studio", "Signoff", "--step", "1", "Proof draft")
    guide.write_text("Proof approved")
    call("remember", "Brand.Studio", str(guide))
    call("dream", "--apply")
    assert len(call("conflicts", "Brand Studio")) == 2
    assert not call("recall", "Brand.Studio", "Proof")
    assert call("quote", "1")[0]["status"] == "conflict"
    assert not call("procedures", "Brand Studio", "Signoff")
    call("remember", "Brand Studio", str(guide))
    assert not call("conflicts", "Brand.Studio")
    assert [r["text"] for r in call("recall", "Brand Studio", "Proof")] == ["Proof approved"]
    assert call("quote", "1")[0]["status"] == "superseded"
    assert call("quote", "2")[0]["status"] == "current"


def test_bounded_cited_paths_stop_at_stale_evidence(tmp_path):
    def call(*args):
        return json.loads(subprocess.check_output([sys.executable, str(SCRIPT), *args], text=True,
                          env={**os.environ, "HERMES_HOME": str(tmp_path)}))
    first, second = tmp_path / "release.md", tmp_path / "owner.md"
    first.write_text("Print proof uses Edition 6.0.")
    second.write_text("Edition 6.0 is assigned to Trial Desk.")
    call("remember", "Studio", str(first))
    call("remember", "Studio", str(second))
    f = ["frame", "Studio", "published edition", "--role", "artifact", "Print proof", "1", first.read_text(),
         "--role", "edition", "Edition 6.0", "1", first.read_text()]
    assert call(*f) == call(*f)
    call("frame", "Studio", "assignment", "--role", "edition", "Edition 6.0", "2", second.read_text(),
         "--role", "owner", "Trial Desk", "2", second.read_text())
    args = ["trace", "Studio", "Print proof"]
    assert [x["hop"] for x in call(*args)] == [1]
    rows = call(*args, "--hops", "2")
    assert [x["hop"] for x in rows] == [1, 2]
    assert rows[1]["roles"][1]["source"] == str(second)
    assert len(call(*args, "--hops", "2", "--limit", "1")) == 1
    limited = call(*args, "--hops", "2", "--limit", "1", "--coverage")
    assert limited["frames"] == rows[:1] and limited["more_matches"]
    exact = call(*args, "--hops", "2", "--limit", "2", "--coverage")
    assert exact["frames"] == rows and not exact["more_matches"]
    one_hop = call(*args, "--hops", "1", "--limit", "1", "--coverage")
    assert len(one_hop["frames"]) == 1 and not one_hop["more_matches"]
    assert not call("trace", "Other", "Print proof", "--hops", "2")
    second.write_text("Edition 6.0 is assigned to Backup Desk.")
    call("remember", "Studio", str(second))
    assert [x["hop"] for x in call(*args, "--hops", "2")] == [1]


def test_long_retrieval_returns_exact_matched_text_and_lines(tmp_path):
    def run(*args):
        return json.loads(subprocess.check_output([sys.executable, str(SCRIPT), *args], text=True,
                          env={**os.environ, "HERMES_HOME": str(tmp_path)}))
    path = tmp_path / "guide.txt"
    for source in ["Intro\r\n" * 700 + "Keep the café proof stamp.\r\n" + "Close\r\n" * 500,
                   "a " * 2500 + "Keep the café proof stamp." + " b" * 2500]:
        path.write_bytes(source.encode())
        run("remember", "Print", str(path))
        hit = run("recall", "Print", "cafe proof")
        full = run("quote", str(hit["record"]))
        left, right = hit["section_characters"]
        assert full["text"][left:right] == hit["text"]
        assert "café proof" in hit["text"] and len(hit["text"]) <= 1800
        start_line = 1 + full["text"][:left].count("\n")
        assert hit["lines"] == [start_line, start_line + len(hit["text"].splitlines()) - 1]


def test_recall_rejects_dummy_auth_aliases_before_storing_source(tmp_path):
    import sqlite3

    profile = tmp_path / "profile"
    profile.mkdir()
    fixture = profile / "auth.json"
    fixture.write_text("ordinary proof fixture")
    note = tmp_path / "proof.txt"
    os.link(fixture, note)
    env = {**os.environ, "HOME": str(tmp_path), "HERMES_HOME": str(profile)}
    def run(*args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, text=True, capture_output=True)
    result = run("remember", "Trial", str(note))
    assert result.returncode != 0 and "authentication stores" in result.stderr
    database, = profile.rglob("*.sqlite")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM docs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM spans").fetchone()[0] == 0
    note.unlink()
    note.write_text("ordinary proof fixture")
    assert run("remember", "Trial", str(note)).returncode == 0
    quoted = json.loads(run("quote", "1").stdout)
    assert quoted["source"] == str(note) and "ordinary proof fixture" in str(quoted)


def test_check_source_compares_disk_without_replacing_historical_citation(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    env = {**os.environ, "HOME": str(tmp_path), "HERMES_HOME": str(profile)}
    def run(*args, status=0):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, text=True, capture_output=True)
        assert result.returncode == status, result.stderr
        return json.loads(result.stdout)
    note = tmp_path / "source.txt"
    note.write_text("Original")
    run("remember", "Studio", str(note))
    database = profile / "project-recall/records.sqlite"
    original_db = database.read_bytes()
    assert run("check-source", "1")["disk_state"] == "matching"
    note.write_text("Revised")
    assert run("check-source", "1", status=2)["disk_state"] == "changed"
    note.unlink()
    assert run("check-source", "1", status=2)["disk_state"] == "missing"
    protected = profile / "auth.json"
    protected.write_text("ordinary fixture")
    os.link(protected, note)
    denied = run("check-source", "1", status=2)
    assert denied["disk_state"] == "unverifiable" and denied["disk_sha256"] is None
    assert database.read_bytes() == original_db
    assert run("quote", "1")["text"] == "Original"


def test_initialized_memory_reads_last_commit_during_import(tmp_path):
    def run(*args):
        proc = subprocess.run([sys.executable, str(SCRIPT), *args], check=True, text=True,
                              capture_output=True, timeout=4, env={**os.environ, "HERMES_HOME": str(tmp_path)})
        return [json.loads(line) for line in proc.stdout.splitlines()]
    source = tmp_path / "route.md"
    source.write_text("# Route\nGo to Copper Desk.")
    admitted, = run("remember", "Print", source)
    row, = run("recall", "Print", "Copper")
    database = tmp_path / "project-recall/records.sqlite"
    with sqlite3.connect(database) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE docs SET digest='uncommitted'")
        assert run("quote", str(row["record"]))[0]["sha256"] == admitted["sha256"]
        assert run("recall", "Print", "Copper") == [row]
        assert run("timeline", "Print", source)[0]["sha256"] == admitted["sha256"]
        assert run("conflicts", "Print") == []
        assert run("procedures", "Print", "route") == []
        assert run("trace", "Print", "Copper") == [[]]
        assert run("dream") == []
        writer.rollback()
    assert run("recall", "Print", "Copper") == [row]
