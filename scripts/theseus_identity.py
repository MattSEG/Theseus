"""Inspect an existing gateway for the local fork without starting a runtime."""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3

from hermes_cli.gateway_runtime import discover_gateway_endpoint


def inspect(profile, discover=discover_gateway_endpoint):
    reported = discover(profile)
    matches = bool(reported.endpoint and reported.endpoint.profile_id == str(profile))
    return {"application": "Theseus Desktop", "expected_profile": str(profile),
            "gateway_state": reported.state, "profile_matches": matches}


def session_receipt(profile, session, expected_model=None):
    database = profile / "state.db"
    if not re.fullmatch(r"local-[0-9a-f]{64}", session):
        return {"status": "invalid local session ID"}
    if not database.is_file():
        return {"status": "session history unavailable"}
    try:
        with sqlite3.connect(database.absolute().as_uri() + "?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session,)).fetchone():
                return {"session": session, "status": "unknown session"}
            # Query recorded usage only. Launch policy and billing endpoints are unnecessary.
            usage = db.execute("""SELECT model,billing_provider='openai-codex',sum(api_call_count)
                FROM session_model_usage WHERE session_id=? AND api_call_count>0
                GROUP BY model,billing_provider ORDER BY model,billing_provider""", (session,)).fetchall()
            tools = db.execute("""SELECT count(*),count(CASE WHEN tool_name='read_file' THEN 1 END),
                count(CASE WHEN tool_name='terminal' THEN 1 END) FROM messages
                WHERE session_id=? AND role='tool'""", (session,)).fetchone()
    except sqlite3.Error:
        return {"session": session, "status": "recorded usage unavailable"}
    observed = []
    for model, subscription, calls in usage:
        # Other routes or custom aliases can contain arbitrary user text.
        known = isinstance(model, str) and re.fullmatch(r"(?:gpt-[0-9]|o[1-9])[A-Za-z0-9._-]{0,65}", model)
        observed.append({"provider": "openai-codex" if subscription else "other or unavailable",
                         "model": model if known and subscription else None, "calls": calls})
    matches = bool(observed) and all(r["provider"] == "openai-codex" and r["model"]
              and (not expected_model or r["model"] == expected_model) for r in observed)
    return {"session": session, "status": "verified" if matches else "unverified",
            "recorded_usage": observed, "stored_tool_results": tools[0],
            "read_file_results": tools[1], "terminal_results": tools[2]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="verify a local gateway session against committed model usage")
    parser.add_argument("--expect-model", help="require every recorded call in the session to use this model")
    args = parser.parse_args()
    if args.expect_model and not args.session:
        parser.error("--expect-model requires --session")
    profile = Path(os.environ["HERMES_HOME"]).resolve()
    result = inspect(profile)
    ok = result["gateway_state"] == "ready" and result["profile_matches"]
    if args.session is not None:
        result["session_receipt"] = session_receipt(profile, args.session, args.expect_model)
        ok = ok and result["session_receipt"]["status"] == "verified"
    print(json.dumps(result))
    raise SystemExit(0 if ok else 2)
