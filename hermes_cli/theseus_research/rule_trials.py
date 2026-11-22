"""One-shot assignment of the causal audit for explicitly enrolled Desktop prompts."""

import hashlib
import re
import sqlite3
from contextlib import closing

ARMS = ("always", "conditional", "absent")
SCHEMA = """CREATE TABLE IF NOT EXISTS assignments (
    prompt_hash TEXT PRIMARY KEY, arm TEXT NOT NULL, session TEXT, turn TEXT, applied INTEGER
)"""
CAUSAL = re.compile(
    r"\b(caus(?:e|ed|al|ation)|explain\w*|why|mechanism|distinguish\w*|attribute\w*|driven by|accounts? for|supports? the claim|evidence for)\b",
    re.IGNORECASE,
)


def key(prompt):
    return hashlib.sha256(prompt.encode()).hexdigest()


def register(home, prompt, arm):
    if arm not in ARMS or not prompt.strip():
        raise ValueError("Supply a nonempty prompt and a recognized assignment")
    folder = home / "learning-runs"
    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder / "rule-trials.sqlite")) as db, db:
        db.execute(SCHEMA)
        db.execute(
            "INSERT INTO assignments(prompt_hash,arm) VALUES(?,?)", (key(prompt), arm)
        )


def apply(home, prompt, session, turn):
    """Default to the installed rule. A second session cannot inherit an assignment."""
    path = home / "learning-runs/rule-trials.sqlite"
    if not path.is_file():
        return True
    digest = key(prompt)
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        row = db.execute(
            "SELECT arm,session,turn,applied FROM assignments WHERE prompt_hash=?",
            (digest,),
        ).fetchone()
        if not row:
            return True
        arm, owner, original_turn, applied = row
        if owner is not None:
            return bool(applied) if (session, turn) == (owner, original_turn) else True
        enabled = arm == "always" or (
            arm == "conditional" and bool(CAUSAL.search(prompt))
        )
        won = db.execute(
            "UPDATE assignments SET session=?,turn=?,applied=? WHERE prompt_hash=? AND session IS NULL",
            (session, turn, enabled, digest),
        ).rowcount
        if won:
            return enabled
        owner, original_turn, applied = db.execute(
            "SELECT session,turn,applied FROM assignments WHERE prompt_hash=?",
            (digest,),
        ).fetchone()
        return bool(applied) if (owner, original_turn) == (session, turn) else True
