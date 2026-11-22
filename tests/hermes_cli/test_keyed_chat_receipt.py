import sqlite3

import pytest

from hermes_cli.keyed_chat_receipt import create_request_id, input_request_id, inspect
from persistence.authority.bindings import local_session_id


def test_offline_receipt_binds_both_ids_and_counts_only_its_session(tmp_path):
    key = "save.fixture"
    principal = "test-local"
    session = local_session_id(str(tmp_path), principal, create_request_id(key))
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("CREATE TABLE session_admissions(target_session_id,principal_id,status,outcome,request_id)")
    db.execute("CREATE TABLE messages(session_id,role,content)")
    db.executemany("INSERT INTO session_admissions VALUES(?,?,?,?,?)", [
        (session, principal, "unknown", None, input_request_id(key)),
        ("different session", principal, "terminal", "completed", input_request_id(key)),
    ])
    db.executemany("INSERT INTO messages VALUES(?,?,?)", [(session, "tool", "private tool result"),
        (session, "user", "private prompt"), ("different session", "tool", "other result")])
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    db.execute("DELETE FROM messages")
    result = inspect(tmp_path, key)
    db.rollback()
    assert result["admission_status"] == "unknown" and result["outcome"] is None
    assert result["session_tool_results"] == result["session_user_messages"] == 1
    assert "private" not in str(result)
    assert "saved artifact" in result["next"]
    with pytest.raises(ValueError, match="no matching"):
        inspect(tmp_path, "new-key")
    another = local_session_id(str(tmp_path), "another", create_request_id(key))
    db.execute("INSERT INTO session_admissions VALUES(?,?,?,?,?)",
               (another, "another", "terminal", "completed", input_request_id(key)))
    db.commit()
    with pytest.raises(ValueError, match="multiple"):
        inspect(tmp_path, key)
    db.close()


def test_absent_profile_and_invalid_key_have_no_effect(tmp_path):
    profile = tmp_path / "unused"
    with pytest.raises(ValueError, match="identifier"):
        inspect(profile, "two words")
    with pytest.raises(ValueError, match="no local session history"):
        inspect(profile, "valid")
    assert not profile.exists()
