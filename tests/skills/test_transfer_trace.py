import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


script = Path(__file__).resolve().parents[2] / "skills/software-development/learning-run/scripts/transfer_trace.py"
spec = importlib.util.spec_from_file_location("transfer_trace", script)
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


@pytest.fixture
def trial(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "learning-runs"
    source = tmp_path / "log.txt"
    source.write_text("measured value")
    policy = {"after_message": 0, "sources": {str(source): trace.digest(source.read_bytes())}}
    db = sqlite3.connect(tmp_path / "state.db")
    db.executescript("""
        CREATE TABLE sessions(id, created_source, parent_session_id, rewind_count, model);
        INSERT INTO sessions VALUES ('live','desktop',NULL,0,'test');
        CREATE TABLE messages(id INTEGER PRIMARY KEY, role, content, tool_calls, tool_call_id,
                              tool_name, active DEFAULT 1, compacted DEFAULT 0, session_id DEFAULT 'live');
    """)
    call = dict(id="call1", function=dict(name="read_file", arguments=json.dumps({"path": str(source)})))
    db.executemany("INSERT INTO messages(role,content,tool_calls,tool_call_id,tool_name) VALUES (?,?,?,?,?)", [
        ("user", "original", None, None, None), ("assistant", "", json.dumps([call]), None, None),
        ("tool", json.dumps({"content": "1|measured value"}), None, "call1", "read_file"),
        ("assistant", "answer", None, None, None),
    ])
    db.commit()
    chat = dict(artifact="chat.txt", artifact_sha256=trace.digest(b"answer"), request_sha256=trace.digest(b"original"))
    yield db, policy, lambda: trace.inspect_trace(root, policy, "live", chat, {"artifact": "saved.md"})
    db.close()


def test_transfer_requires_registered_returned_evidence(trial):
    db, policy, inspect = trial
    assert inspect()["status"] == "pass"
    policy["sources"] = {}
    assert inspect()["status"] == "fail"


@pytest.mark.parametrize("sql", [
    "UPDATE messages SET content='different request' WHERE id=1",
    "UPDATE messages SET content='different answer' WHERE id=4",
    "UPDATE messages SET tool_call_id='orphan' WHERE id=3",
    """UPDATE messages SET content='{"content":"1|wrong"}' WHERE id=3""",
    "UPDATE messages SET compacted=1 WHERE id=2",
    "DELETE FROM messages WHERE id=3",
    "UPDATE sessions SET parent_session_id='another'",
])
def test_missing_or_changed_trace_cannot_pass(trial, sql):
    db, _, inspect = trial
    with db:
        db.execute(sql)
    assert inspect()["status"] == "unverified"


def test_posthoc_source_registration_cannot_pass(trial):
    _, policy, inspect = trial
    policy["after_message"] = 1
    assert inspect()["status"] == "unverified"


def test_missing_trace_overrides_clean_transfer_content(tmp_path, monkeypatch):

    monkeypatch.syspath_prepend(str(script.parent))
    import observations
    import run

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    root, db = run.journal()
    with db:
        db.execute("INSERT INTO events VALUES (1,'trial','baseline','now','{}')")
        prior = dict(case="previous", criteria=["correct"], choice="transfer", request_sha256="old", gaps=[])
        db.execute("INSERT INTO observations VALUES(1,'previous',?)", (json.dumps(prior),))
    def receipt(root, path):
        proof = dict(acceptance_criteria=["correct"], request_sha256="new", session=str(path),
                     artifact=str(path), checks=[dict(requirement="correct", status="pass")])
        return proof, {"path": str(path)}
    monkeypatch.setattr(observations, "reviewed", receipt)
    result = observations.observe(root, db, 1, "held-out", Path("answer"), Path("saved"))
    assert result["choice"] == "verify"
    assert result["trace"]["status"] == "unverified"
    db.close()
