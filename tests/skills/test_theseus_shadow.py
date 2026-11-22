import sqlite3
from contextlib import closing

from hermes_cli import theseus_shadow as shadow


def test_shadow_is_scoped_and_retries_preserve_the_prospective_record(tmp_path):
    def unexpected():
        raise AssertionError("disabled watch evaluated the callback")

    shadow.propose(tmp_path, "resume", "a/job", "revision", unexpected)
    root = tmp_path / "shadow"
    assert not root.exists()
    root.mkdir()
    path = root / "history.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(shadow.SCHEMA)
        db.execute("INSERT INTO watches VALUES('resume','a/job')")
    shadow.propose(tmp_path, "resume", "other/job", "revision", unexpected)
    for current in (2, 3):
        shadow.propose(tmp_path, "resume", "a/job", "revision", lambda: {
            "draft_claims": {"route": 1}, "current_claims": {"route": current}})
    shadow.outcome(tmp_path, "resume", "a/job", "revision", lambda: {"observed": 2})
    shadow.outcome(tmp_path, "resume", "a/job", "revision", unexpected)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT json_extract(context,'$.current_claims.route') FROM decisions").fetchall() == [(2,)]
        assert db.execute("SELECT json_extract(evidence,'$.observed') FROM outcomes").fetchall() == [(2,)]


def test_source_shadow_can_request_a_checked_comparison_while_abstaining():
    state = {"direct_candidate": False, "unshown_comparisons": ["record:sample"]}
    options, selected, criterion = shadow.policy("source", state)
    assert selected == options[2]
    assert "nearby results" in criterion
    state["unshown_comparisons"] = []
    assert shadow.policy("source", state)[1] == options[1]
