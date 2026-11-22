import json
import os
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[2] / "skills/software-development/learning-run/scripts/run.py"


def test_retrieve_failed_attempt_and_preserve_decision(tmp_path):
    def invoke(*args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], check=False, capture_output=True,
                                text=True, env={**os.environ, "HERMES_HOME": str(tmp_path)})
        assert (result.returncode == 0) == success, result.stderr
        return result.stdout

    invoke("begin", "Export", "--baseline", "0/1")
    invoke("review", "1", "--decision", "keep", "--reason", "premature", success=False)
    invoke("propose", "1", "--change", "suffix", "--rationale", "format", "--expected", "1/1")
    invoke("evaluate", "1", "--diff", "<script>", "--candidate", "0/1", "--fresh", "0/2",
           "--recommendation", "revert")
    invoke("review", "1", "--decision", "revert", "--reason", "failed fresh cases")
    invoke("review", "1", "--decision", "keep", "--reason", "overwrite", success=False)
    output = invoke("begin", "export", "--baseline", "0/2")
    prior = json.loads(output.rsplit("\n", 2)[0])
    assert prior[-1]["details"]["decision"] == "revert"
    report = Path(invoke("show", "1").strip()).read_text()
    assert "&lt;script&gt;" in report and "failed fresh cases" in report
    assert invoke("begin", "unrelated", "--baseline", "0/1").startswith("[]")


def test_resume_after_process_death(tmp_path):
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    begin = ["begin", "receipts", "--job", "batch-6", "--baseline", "one request"]
    # Kill the actual CLI process after it commits, while publishing its report.
    crash = """import os,runpy,signal,sys
def stop(src,dst):
    os.kill(os.getpid(),signal.SIGKILL)
os.replace=stop
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", crash, str(SCRIPT), *begin], env=env, check=False)
    assert result.returncode < 0

    def invoke(*args):
        return subprocess.check_output([sys.executable, str(SCRIPT), *args], env=env, text=True)
    receipt = json.loads(invoke("resume", "batch-6"))
    assert receipt["report_state"] == "restored" and receipt["next"] == "propose"
    path = Path(receipt["report"])
    expected = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    assert json.loads(invoke("resume", "batch-6"))["report_state"] == "verified"
    assert path.stat().st_mtime_ns == stamp
    assert Path(invoke(*begin).splitlines()[-1]) == path
    assert not list(path.parent.glob("*.part"))
    path.write_bytes(b"truncated")
    invoke("resume", "batch-6")
    assert path.read_bytes() == expected
    collision = subprocess.run([sys.executable, str(SCRIPT), *begin[:-1], "different"],
                               env=env, capture_output=True, check=False)
    assert collision.returncode != 0
    assert list(path.parent.glob("run-*.html")) == [path]


def test_simultaneous_retries_share_one_report(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    def submit(_):
        return subprocess.check_output([sys.executable, str(SCRIPT), "begin", "review",
                                        "--job", "shared", "--baseline", "one"], text=True, env=env)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(submit, range(12)))
    assert len(set(results)) == 1
    assert len(list((tmp_path / "learning-runs").glob("run-*.html"))) == 1


def test_job_index_rebuilds_from_journal_after_interruption(tmp_path):
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    def run(*args):
        return subprocess.check_output([sys.executable, str(SCRIPT), *args], text=True, env=env)
    run("begin", "one", "--baseline", "original")
    run("begin", "two", "--baseline", "other", "--job", "second")
    path = tmp_path / "learning-runs/index.json"
    path.write_text("partial write")
    run("propose", "1", "--change", "patch", "--rationale", "because", "--expected", "observed")
    rows = json.loads(path.read_text())
    assert [(row["run"], row["phase"]) for row in rows] == [(2, "baseline"), (1, "proposal")]
    assert all(row["decision"] is None for row in rows)
    assert [row["job"] for row in rows] == ["second", None]


def test_semantic_lookup_preserves_failed_outcome_and_cross_problem_retry(tmp_path):
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    def call(*args):
        return subprocess.check_output([sys.executable, str(SCRIPT), *args], env=env, text=True)
    call("begin", "print fixture", "--baseline", "output receipt is absent")
    call("propose", "1", "--change", "retry immediately", "--rationale", "missing receipt", "--expected", "one copy")
    call("evaluate", "1", "--diff", "test", "--candidate", "two copies", "--fresh", "duplicate output", "--recommendation", "revert")
    before = json.loads(call("find", "missing receipts"))[0]
    assert before["decision"] is None and before["decision_reason"] is None
    reason = "The bindery rejected the collation."
    call("review", "1", "--decision", "revert", "--reason", reason)
    by_reason, = json.loads(call("find", "bindery"))
    assert by_reason["run"] == 1 and by_reason["decision_reason"] == reason
    assert "[bindery]" in by_reason["excerpt"]
    result = json.loads(call("find", "missing receipts"))[0]
    assert result["run"] == 1 and result["recommendation"] == result["decision"] == "revert"
    args = ["begin", "delivery", "--baseline", "count outputs", "--prior", "1", "--job", "newdelivery"]
    first = call(*args)
    second = call(*args)
    assert first == second and '"decision": "revert"' in first
    assert 'href="run-1.html"' in Path(first.splitlines()[-1]).read_text()


def test_retry_replays_only_information_available_at_admission(tmp_path):
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    def call(*args):
        return subprocess.check_output([sys.executable, str(SCRIPT), *args], env=env, text=True)
    call("begin", "First", "--baseline", "a")
    call("propose", "1", "--change", "b", "--rationale", "c", "--expected", "d")
    call("evaluate", "1", "--candidate", "e", "--fresh", "f", "--diff", "g", "--recommendation", "keep")
    replay = ["begin", "Second", "--job", "stable", "--prior", "1", "--baseline", "start"]
    original = call(*replay)
    call("review", "1", "--decision", "revert", "--reason", "late observation")
    assert call(*replay) == original
    assert [r["phase"] for r in json.loads(call("evidence", "2"))] == ["baseline", "proposal", "evaluation"]
    call("begin", "First", "--baseline", "new case")
    assert json.loads(call("evidence", "3"))[-1]["details"]["decision"] == "revert"


def test_explicit_supersession_preserves_old_conclusion_and_repairs_report(tmp_path):
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    def call(*args, ok=True):
        r = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True)
        assert (r.returncode == 0) == ok, r.stderr
        return r.stdout
    for i in (1, 2, 3):
        call("begin", "Print", "--baseline", "receipt")
        call("propose", str(i), "--change", "selection", "--expected", "one", "--rationale", "trial")
        call("evaluate", str(i), "--diff", "patch", "--candidate", "receipt", "--fresh", "trial", "--recommendation", "keep")
        if i == 1:
            call("review", "1", "--decision", "keep", "--reason", "original")
    call("review", "2", "--decision", "revert", "--reason", "later finding", "--supersedes", "1")
    one = tmp_path / "learning-runs/run-1.html"
    assert 'Superseded by</h2><a href="run-2.html"' in one.read_text()
    one.write_text("damaged")
    call("show", "2")
    assert "original" in one.read_text() and "Superseded by" in one.read_text()
    old = next(r for r in json.loads(call("find", "receipt")) if r["run"] == 1)
    assert old["decision"] == "keep" and old["superseded_by"] == 2
    assert json.loads(call("evidence", "2"))[-1]["details"]["decision"] == "keep"
    call("review", "3", "--decision", "keep", "--reason", "stale", "--supersedes", "1", ok=False)


def test_evaluation_links_and_detects_changed_or_missing_evidence(tmp_path):
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    def call(*args, code=0):
        r = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True)
        assert r.returncode == code, r.stderr
        return r.stdout
    f = tmp_path / "test & evidence.txt"
    f.write_text("original")
    call("begin", "print", "--baseline", "old")
    call("propose", "1", "--change", "small", "--expected", "ok", "--rationale", "why")
    output = call("evaluate", "1", "--diff", "patch", "--candidate", "ok", "--fresh", "fresh",
                  "--recommendation", "keep", "--file", "Proof & result", str(f))
    html = Path(output.strip()).read_text()
    assert f.as_uri() in html and "Proof &amp; result" in html
    assert "original" not in html
    def check(code):
        return json.loads(call("check-files", "1", code=code))["files"][0]["state"]
    assert check(0) == "current"
    f.write_text("modified")
    assert check(2) == "changed"
    f.unlink()
    assert check(2) == "missing"


def test_staged_patch_remains_visible_before_and_after_tests(tmp_path):
    def invoke(*args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                                env={**os.environ, "HERMES_HOME": str(tmp_path)})
        assert (result.returncode == 0) == success, result.stderr
        return result.stdout

    for number, changed in enumerate((False, True), start=1):
        run = str(number)
        invoke("begin", "stage", "--baseline", "observed", "--job", "key" + run)
        invoke("stage", run, "--diff", "early", success=False)
        invoke("propose", run, "--change", "patch", "--expected", "pass", "--rationale", "observed")
        evaluate = ("evaluate", run, "--candidate", "passed", "--fresh", "passed", "--recommendation", "keep")
        invoke(*evaluate, success=False)
        invoke("stage", run, "--diff", "  ", success=False)
        invoke("stage", run, "--diff", "proposed <version>")
        invoke("stage", run, "--diff", "overwrite", success=False)
        assert "evaluate" in invoke("resume", "key" + run)
        def report():
            return Path(invoke("show", run).strip()).read_text()
        assert "proposed &lt;version&gt;" in report()
        invoke(*evaluate, *(("--diff", "tested replacement") if changed else ()))
        content = report()
        assert content.count("proposed &lt;version&gt;") == 1
        assert ("tested replacement" in content) == changed
        assert ("changed after staging" in content) == changed


def test_brief_history_preserves_failed_evidence_and_original_patch(tmp_path):
    import hashlib

    def invoke(*args):
        return subprocess.check_output([sys.executable, str(SCRIPT), *args], text=True,
                                      env={**os.environ, "HERMES_HOME": str(tmp_path)})

    patch = "−original\n+épreuve"
    invoke("begin", "brief export", "--baseline", "failed")
    invoke("propose", "1", "--change", "adjust", "--rationale", "format", "--expected", "pass")
    invoke("evaluate", "1", "--candidate", "failed", "--fresh", "failed again", "--diff", patch,
           "--recommendation", "revert")
    invoke("review", "1", "--decision", "revert", "--reason", "observed fresh failure")
    args = ("begin", "brief export", "--baseline", "repeat", "--job", "retry")
    out = invoke(*args, "--brief")
    assert invoke(*args, "--brief") == out
    brief = json.loads(out.rsplit("\n", 2)[0])
    assert brief == json.loads(invoke("evidence", "2", "--brief"))
    full = json.loads(invoke("evidence", "2"))
    assert full[2]["details"].pop("diff") == patch
    assert brief[2]["details"].pop("patch reference") == {
        "bytes": len(patch.encode()), "sha256": hashlib.sha256(patch.encode()).hexdigest()}
    assert brief == full
    assert brief[-1]["details"] == {"decision": "revert", "reason": "observed fresh failure"}


def test_journal_checks_identity_of_evidence_at_registration_and_recheck(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    env = {**os.environ, "HOME": str(tmp_path), "HERMES_HOME": str(profile)}
    def invoke(*args, status=0):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, text=True, capture_output=True)
        assert result.returncode == status, result.stderr
        return result.stdout

    original = profile / "auth.json"
    original.write_text("plain fixture")
    alias = tmp_path / "score.json"
    os.link(original, alias)
    invoke("begin", "evidence", "--baseline", "fixture")
    invoke("propose", "1", "--change", "patch", "--rationale", "test", "--expected", "pass")
    evaluate = ("evaluate", "1", "--diff", "patch", "--candidate", "pass", "--fresh", "pass",
                "--recommendation", "keep", "--file", "Score", str(alias))
    invoke(*evaluate, status=2)
    alias.unlink()
    alias.write_text("plain fixture")
    invoke(*evaluate)
    assert "current" in invoke("check-files", "1")
    alias.unlink()
    os.link(original, alias)
    assert "unverifiable" in invoke("check-files", "1", status=2)


def test_job_inspection_never_creates_or_repairs_reports(tmp_path):
    profile = tmp_path / "empty profile"
    env = {**os.environ, "HERMES_HOME": str(profile)}

    def run(*args, status=0):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True, env=env)
        assert result.returncode == status, result.stderr
        return result.stdout

    run("inspect", "unknown", status=2)
    assert not profile.exists()
    run("begin", "Print", "--baseline", "missing", "--job", "exact-Key")
    run("inspect", "exact-key", status=2)
    assert json.loads(run("inspect", "exact-Key"))["next"] == "propose"
    run("propose", "1", "--change", "name", "--expected", "pass", "--rationale", "print")
    run("evaluate", "1", "--diff", "+name", "--candidate", "pass", "--fresh", "pass", "--recommendation", "keep")
    pending = json.loads(run("inspect", "exact-Key"))
    assert pending["recommendation"] == "keep" and pending["decision"] is None and pending["next"] == "human review"
    run("review", "1", "--decision", "revert", "--reason", "operator declined")
    report = profile / "learning-runs" / "run-1.html"
    report.unlink()
    database = report.parent / "history.sqlite"
    before = database.read_bytes()
    result = json.loads(run("inspect", "exact-Key"))
    assert result["decision"] == "revert" and result["recommendation"] == "keep" and result["next"] == "complete"
    assert not report.exists() and database.read_bytes() == before


def test_patch_correction_survives_rebuild_without_rewriting_original_events(tmp_path):
    import sqlite3

    def call(*args, status=0):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], env={**os.environ, "HERMES_HOME": str(tmp_path)},
                                capture_output=True, text=True)
        assert result.returncode == status, result.stderr
        return result.stdout.strip()
    for number in ("1", "2"):
        call("begin", "patch review", "--baseline", "observed")
        call("propose", number, "--change", "source", "--rationale", "compare", "--expected", "verified")
        call("evaluate", number, "--diff", "patch" + number, "--candidate", "result", "--fresh", "result",
             "--recommendation", "keep")
    with sqlite3.connect(tmp_path / "learning-runs/history.sqlite") as db:
        original = db.execute("SELECT * FROM events ORDER BY rowid").fetchall()
        call("flag-diff", "2", "--see", "1", status=2)
        report = Path(call("flag-diff", "1", "--see", "2"))
        report.unlink()
        call("flag-diff", "1", "--see", "2")
        assert db.execute("SELECT * FROM events ORDER BY rowid").fetchall() == original
        assert db.execute("SELECT count(*) FROM patch_corrections").fetchone()[0] == 1
        assert 'Patch evidence correction' in report.read_text() and 'patch1' in report.read_text()


def test_history_replays_corrections_as_known_when_the_job_started(tmp_path):
    def call(*args):
        return subprocess.check_output([sys.executable, str(SCRIPT), *args], text=True,
                                      env={**os.environ, "HERMES_HOME": str(tmp_path)})
    for n in ("1", "2"):
        call("begin", "print", "--baseline", "observe", "--job", "job" + n)
        call("propose", n, "--change", "patch", "--rationale", "fix", "--expected", "pass")
        call("evaluate", n, "--diff", "source", "--candidate", "fixed index", "--fresh", "pass",
             "--recommendation", "keep")
    call("flag-diff", "1", "--see", "2")
    assert json.loads(call("inspect", "job1"))["patch_correction_runs"] == [2]
    old = json.loads(call("evidence", "2", "--brief"))
    assert all("patch_correction_runs" not in r for r in old)
    args = ("begin", "print", "--baseline", "fresh", "--job", "later", "--brief")
    admitted = call(*args)
    rows = json.loads(admitted.rsplit("\n", 2)[0])
    assert rows == json.loads(call("evidence", "3", "--brief"))
    assert [r["patch_correction_runs"] for r in rows if "patch_correction_runs" in r] == [[2]]
    assert call(*args) == admitted
    call("propose", "3", "--change", "patch", "--rationale", "fix", "--expected", "pass")
    call("evaluate", "3", "--diff", "new", "--candidate", "tested", "--fresh", "pass", "--recommendation", "keep")
    call("flag-diff", "1", "--see", "3")
    assert json.loads(call("inspect", "job1"))["patch_correction_runs"] == [2, 3]
    assert rows == json.loads(call("evidence", "3", "--brief"))


def test_frozen_patch_ignores_working_index_and_moving_refs(tmp_path):
    import sqlite3

    repo = tmp_path / "repo"
    repo.mkdir()
    profile = tmp_path / "profile"
    env = {**os.environ, "HERMES_HOME": str(profile), "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@localhost",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@localhost"}
    def git(*args):
        return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo, env=env, text=True)
    def journal(*args, status=0):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=repo, env=env, text=True, capture_output=True)
        assert result.returncode == status, result.stderr
        return result.stdout.strip()
    git("init", "-q")
    git("commit", "--allow-empty", "-qm", "base")
    base = git("rev-parse", "HEAD").strip()
    name = "new source.py"
    (repo / name).write_text("first = True\n")
    git("add", "--", name)
    git("commit", "-qm", "candidate")
    commit = git("rev-parse", "HEAD").strip()
    git("tag", "candidate", commit)
    git("switch", "--detach", "-q", base)
    (repo / name).write_text("unrelated working bytes\n")
    journal("begin", "compare", "--baseline", "observed")
    journal("propose", "1", "--change", "patch", "--rationale", "test", "--expected", "correct")
    options = ("stage", "1", "--between", base, "candidate")
    journal(*options, "--path", "no-such-file", status=2)
    journal(*options, "--path", "../outside", status=2)
    journal(*options, "--path", name)
    git("tag", "-f", "candidate", base)
    report = Path(journal("show", "1")).read_text()
    with sqlite3.connect(profile / "learning-runs/history.sqlite") as db:
        details = json.loads(db.execute("SELECT details FROM events WHERE phase='staging'").fetchone()[0])
    assert details["source revisions"] == {"base": base, "candidate": commit, "paths": [name]}
    assert details["diff"] == git("diff", base, commit, "--", name)
    assert "new file mode" in details["diff"] and "unrelated working bytes" not in details["diff"]
    assert commit in report and "Source revisions" in report
    assert git("ls-files", "--", name) == ""


def test_separator_variants_recall_the_same_frozen_history(tmp_path):
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    def call(*args):
        return subprocess.check_output([sys.executable, str(SCRIPT), *args], env=env, text=True)
    for problem in ("Scan_Files", "scan-files", "scanned files", "scan C++", "scan C"):
        call("begin", problem, "--baseline", "first")
    args = ["begin", "SCAN   FILES", "--baseline", "second", "--job", "variant"]
    first = call(*args)
    rows = json.loads(first.rsplit("\n", 2)[0])
    assert [row["run"] for row in rows] == [1, 2]
    call("propose", "2", "--change", "later", "--rationale", "new", "--expected", "better")
    args[1] = "scan-files"
    assert call(*args) == first
    different = call("begin", "scan C", "--baseline", "third")
    assert [row["run"] for row in json.loads(different.rsplit("\n", 2)[0])] == [5]


def test_report_handoff_includes_job_commands_and_escaped_operator_steps(tmp_path):
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    def call(*args):
        return subprocess.check_output([sys.executable, str(SCRIPT), *args], env=env, text=True)
    call("begin", "proof", "--baseline", "empty")
    assert "./theseus learning evidence 1 --brief" in (tmp_path / "learning-runs/run-1.html").read_text()
    call("begin", "proof", "--job", "saved.edition-2", "--baseline", "unverified")
    call("propose", "2", "--change", "inspect saved", "--rationale", "verify", "--expected", "one write")
    fixture = tmp_path / "saved.txt"
    fixture.write_text("one write")
    report = Path(call("evaluate", "2", "--candidate", "one", "--fresh", "two", "--diff", "fixture",
                       "--recommendation", "keep", "--try", "Open <preview>.", "--next", "Change the edition.",
                       "--file", "saved", str(fixture)).strip())
    result = report.read_text()
    assert "Open &lt;preview&gt;." in result and "Change the edition." in result
    assert "./theseus learning inspect saved.edition-2" in result and "./theseus learning check-files 2" in result
    assert json.loads(call("inspect", "saved.edition-2"))["run"] == 2
    assert json.loads(call("check-files", "2"))["registered"] == 1


def test_file_audit_checks_committed_evaluations_without_creating_a_journal(tmp_path):
    import sqlite3

    profile = tmp_path / "profile"
    env = {**os.environ, "HERMES_HOME": str(profile)}
    def run(*args, status=0):
        p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=4)
        assert p.returncode == status, p.stderr
        return p.stdout

    assert json.loads(run("audit", status=2))["evaluations"] == 0
    run("check-files", "7", status=2)
    assert not profile.exists()
    first, second = tmp_path / "saved.txt", tmp_path / "result.txt"
    first.write_text("saved")
    second.write_text("passed")
    for number in ("1", "2"):
        run("begin", "file review", "--baseline", "baseline")
        run("propose", number, "--change", "review", "--rationale", "inspect", "--expected", "pass")
        refs = ("--file", "artifact", str(first), "--file", "test", str(second)) if number == "2" else ()
        run("evaluate", number, "--candidate", "test", "--fresh", "fresh", "--diff", "evidence only",
            "--recommendation", "keep", *refs)
    with sqlite3.connect(profile/"learning-runs/history.sqlite") as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("DELETE FROM events WHERE run=2")
        assert json.loads(run("audit", "--from-run", "2"))["file_states"] == {"current": 2}
        assert json.loads(run("check-files", "2"))["registered"] == 2
        writer.rollback()
    first.write_text("changed")
    second.unlink()
    checked = json.loads(run("audit", status=2))
    assert checked["without_registered_files"] == [1]
    assert checked["file_states"] == {"changed": 1, "missing": 1}
    assert [(p["run"], p["state"]) for p in checked["problems"]] == [(2, "changed"), (2, "missing")]
    assert not json.loads(run("audit", "--from-run", "3", status=2))["complete"]


def test_adaptive_observation_diagnoses_disagreement_and_checks_transfer():
    import runpy

    choose = runpy.run_path(str(SCRIPT.with_name("observations.py")))["next_test"]
    passed, failed, uncertain = ({"original": s} for s in ("pass", "fail", "unverified"))
    for chat, artifact, expected in [
        (passed, failed, "render"), (failed, passed, "context"),
        (failed, failed, "ledger"), (uncertain, failed, "verify"),
        (passed, passed, "stress"),
    ]:
        assert choose(chat, artifact, None)[0] == expected
    assert choose(passed, passed, {"choice": "render"})[0] == "transfer"
    assert choose(passed, failed, {"choice": "transfer"})[0] == "render"
    assert choose(passed, passed, {"choice": "transfer"})[0] == "stop"
