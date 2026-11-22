"""Read a finite chat's committed admission without connecting to a gateway."""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from persistence.authority.admissions import AdmissionOutcome, AdmissionStatus
from persistence.authority.bindings import local_session_id


REQUEST_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")


def create_request_id(key):
    return uuid.uuid5(uuid.NAMESPACE_URL, "theseus/finite/create/" + key).hex


def input_request_id(key):
    return uuid.uuid5(uuid.NAMESPACE_URL, "theseus/finite/input/" + key).hex


def inspect(profile, key):
    if not REQUEST_KEY.fullmatch(key):
        raise ValueError("supply a short job identifier")
    profile = profile.resolve()
    database = profile / "state.db"
    if not database.is_file():
        raise ValueError("no local session history")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        rows = db.execute("""SELECT target_session_id,principal_id,status,outcome FROM session_admissions
                             WHERE request_id=?""", (input_request_id(key),))
        matches = [(session, state, outcome) for session, principal, state, outcome in rows
                   if session == local_session_id(str(profile), principal, create_request_id(key))]
        if not matches:
            raise ValueError("no matching keyed admission in this profile")
        if len(matches) != 1:
            raise ValueError("multiple matching keyed admissions in this profile")
        session, state, outcome = matches[0]
        try:
            state = AdmissionStatus(state)
            outcome = AdmissionOutcome(outcome) if outcome is not None else None
        except ValueError:
            raise ValueError("unrecognized stored admission state") from None
        users, tools = db.execute("""SELECT count(CASE WHEN role='user' THEN 1 END),
            count(CASE WHEN role='tool' THEN 1 END) FROM messages WHERE session_id=?""", (session,)).fetchone()
    result = {"session": session, "admission_status": state, "outcome": outcome,
              "session_user_messages": users, "session_tool_results": tools}
    if state == AdmissionStatus.UNKNOWN:
        result["next"] = "Inspect the saved artifact or its delivery receipt before submitting a new request."
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", help="the --request-key used by the original structured chat")
    args = parser.parse_args()
    try:
        result = inspect(Path(os.environ["HERMES_HOME"]), args.key)
    except (ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
