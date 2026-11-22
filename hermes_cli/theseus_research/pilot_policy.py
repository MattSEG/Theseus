"""Operator-reviewed comparison instructions, scoped to this profile and comparison task."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone, UTC

TABLE = """CREATE TABLE IF NOT EXISTS pilot_policy_events(
    id INTEGER PRIMARY KEY, run INTEGER NOT NULL, action TEXT NOT NULL,
    before_rule TEXT NOT NULL, after_rule TEXT NOT NULL, recorded TEXT NOT NULL)"""


def head(db):
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pilot_policy_events'"
    ).fetchone()
    row = (
        db.execute(
            "SELECT id,run,action,after_rule FROM pilot_policy_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if exists
        else None
    )
    return (
        {"id": row[0], "run": row[1], "action": row[2], "rule": row[3]}
        if row
        else {"id": 0, "rule": ""}
    )


def current(home):
    path = home / "learning-runs/history.sqlite"
    if not path.exists():
        return {"id": 0, "rule": ""}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return head(db)


def update(home, run, expected_head, rule=None):
    """Commit an approval or restoration and its ledger event under the same write lock."""
    path = home / "learning-runs/history.sqlite"
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(TABLE)
        old = head(db)
        if old["id"] != expected_head:
            raise ValueError(
                "The active comparison instructions changed. Recheck the current version."
            )
        now = datetime.now(UTC).isoformat()
        if rule is None:
            if old.get("action") != "apply" or old["run"] != run:
                raise ValueError("This instruction is no longer the active change.")
            next_rule = db.execute(
                "SELECT before_rule FROM pilot_policy_events WHERE id=?", (old["id"],)
            ).fetchone()[0]
            action = "restore"
        else:
            events = {
                phase: json.loads(details)
                for phase, details in db.execute(
                    "SELECT phase,details FROM events WHERE run=?", (run,)
                )
            }
            if (
                events.get("evaluation", {}).get("recommendation") != "keep"
                or events.get("review", {}).get("decision") == "revert"
            ):
                raise ValueError("This experiment is ineligible for adoption.")
            next_rule = "\n".join(filter(None, (old["rule"], rule)))
            if not rule.strip() or len(next_rule) > 2400:
                raise ValueError(
                    "The resulting instructions exceed the reviewed policy bound."
                )
            action = "apply"
            if "review" not in events:
                reason = (
                    "Operator approved the tested comparison instruction in Desktop."
                )
                db.execute(
                    """INSERT INTO events(run,problem,phase,recorded,details)
                    SELECT run,problem,'review',?,? FROM events WHERE run=? AND phase='evaluation'""",
                    (now, json.dumps({"decision": "keep", "reason": reason}), run),
                )
        db.execute(
            "INSERT INTO pilot_policy_events(run,action,before_rule,after_rule,recorded) VALUES(?,?,?,?,?)",
            (run, action, old["rule"], next_rule, now),
        )
        return head(db)
