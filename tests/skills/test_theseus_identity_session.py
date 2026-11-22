import json
import sqlite3

from scripts.theseus_identity import session_receipt


def test_attribution_requires_recorded_calls_and_only_discloses_allowed_fields(tmp_path):
    session = "local-" + "a" * 64
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.executescript("""CREATE TABLE sessions(id,model);
            CREATE TABLE session_model_usage(session_id,model,billing_provider,api_call_count,billing_base_url);
            CREATE TABLE messages(session_id,role,tool_name,content);""")
        db.execute("INSERT INTO sessions VALUES(?,?)", (session, "gpt-6.2-astra"))
        db.commit()
        assert session_receipt(tmp_path, session)["status"] == "unverified"
        db.execute("INSERT INTO session_model_usage VALUES(?,?,?,?,?)", (session, "gpt-6.2-astra", "openai-codex", 2, "private-endpoint"))
        db.execute("INSERT INTO messages VALUES(?,?,?,?)", (session, "tool", "read_file", "private-tool-content"))
        db.commit()
        good = session_receipt(tmp_path, session, "gpt-6.2-astra")
        assert good["status"] == "verified" and good["read_file_results"] == 1
        assert good["recorded_usage"] == [{"provider": "openai-codex", "model": "gpt-6.2-astra", "calls": 2}]
        assert session_receipt(tmp_path, session, "gpt-different")["status"] == "unverified"
        db.execute("INSERT INTO session_model_usage VALUES(?,?,?,?,?)", (session, "private-model-alias", "private-provider", 1, "private-endpoint"))
        db.commit()
        mixed = session_receipt(tmp_path, session)
        assert mixed["status"] == "unverified" and "private-" not in json.dumps(mixed)
        assert session_receipt(tmp_path, "local-" + "b" * 64)["status"] == "unknown session"


def test_missing_store_is_never_created(tmp_path):
    assert session_receipt(tmp_path, "local-" + "0" * 64)["status"] == "session history unavailable"
    assert not (tmp_path / "state.db").exists()
    assert session_receipt(tmp_path, "not-an-id")["status"] == "invalid local session ID"
