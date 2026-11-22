"""Prospective follow-up for a passage setting selected from an ordinary export."""

import json
import time

from . import search_focus, search_trials

EXPORT_LIMIT = 3
ATTEMPT_LIMIT = 12
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS search_choice_followups(
        choice INTEGER PRIMARY KEY,project TEXT NOT NULL,origin INTEGER NOT NULL,cutoff INTEGER NOT NULL,arm TEXT NOT NULL,
        version TEXT NOT NULL,source_keys TEXT NOT NULL,created INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS search_choice_observations(
        choice INTEGER NOT NULL,job INTEGER NOT NULL,kind TEXT NOT NULL,eligible INTEGER NOT NULL,
        evidence TEXT NOT NULL,PRIMARY KEY(choice,job))""",
)


def initialize(db):
    for ddl in SCHEMA:
        db.execute(ddl)


def keys(c):
    return {
        "source:" + str(c["source"]),
        "body:" + c["digest"],
        "quote:" + c["quote_digest"],
    }


def enroll(db, project, choice, origin):
    initialize(db)
    result = json.loads(origin["result"])
    material = sorted(set().union(*(keys(c) for c in result["cells"])))
    cutoff = db.execute(
        "SELECT max(id) FROM research_assist_exports WHERE project=?", (project,)
    ).fetchone()[0]
    db.execute(
        "INSERT OR IGNORE INTO search_choice_followups VALUES(?,?,?,?,?,?,?,?)",
        (
            choice["id"],
            project,
            origin["id"],
            cutoff,
            "focused" if choice["focused"] else "current",
            search_focus.VERSION,
            json.dumps(material),
            int(time.time()),
        ),
    )


def available(db, choice):
    if (
        not choice
        or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='search_choice_followups'"
        ).fetchone()
    ):
        return None
    return db.execute(
        """SELECT f.* FROM search_choice_followups f WHERE choice=? AND version=?
        AND (SELECT count(*) FROM search_choice_observations o WHERE o.choice=f.choice)<?
        AND (SELECT coalesce(sum(eligible),0) FROM search_choice_observations o WHERE o.choice=f.choice)<?""",
        (choice, search_focus.VERSION, ATTEMPT_LIMIT, EXPORT_LIMIT),
    ).fetchone()


def ordinary_search(db, project, args, session):
    # The setting, the search and its collection receipt share the same SQLite snapshot.
    if not db.in_transaction:
        db.execute("BEGIN")
    choice = search_trials.project_choice(db, project)
    from .library import search

    result = search(db, project, args, focused=choice["focused"])
    from .assistance import mode

    if (
        session
        and choice["id"]
        and available(db, choice["id"])
        and mode(db, project) != "off"
    ):
        roster = search_trials.collection(db, project)
        if len(roster) <= search_trials.MAX_EDITIONS:
            result["field_check"] = dict(
                choice=choice["id"],
                arm="focused" if choice["focused"] else "current",
                collection=search_trials.fingerprint(roster),
                version=search_focus.VERSION,
            )
    return result


def grade(result, arm, seen):
    alternate = "current" if arm == "focused" else "focused"
    cells = result["cells"]
    proof = dict(
        source_keys=sorted(set().union(*(keys(c) for c in cells))),
        gained=[],
        lost=[],
        reads=result.get("source_reads"),
        checked=len(cells),
    )
    if result.get("other_authoring_tools"):
        proof["reason"] = "Other authoring tools were used: " + ", ".join(
            result["other_authoring_tools"][:8]
        )
        return "unverified", False, proof
    if not cells or any(
        not c["current"]
        or not (c["original_read"] or c["search"]["original"])
        or c["search"]["original"] != c["search"][arm]
        for c in cells
    ):
        return "unverified", False, proof
    novel = [c for c in cells if keys(c).isdisjoint(seen)]

    def cell_ref(c):
        return dict(
            row=c["row"], column=c["column"], edition=c["edition"], title=c["title"]
        )

    proof["gained"] = [
        cell_ref(c)
        for c in novel
        if c["search"]["original"] and not c["search"][alternate]
    ]
    proof["lost"] = [
        cell_ref(c)
        for c in cells
        if not c["search"]["original"] and c["search"][alternate]
    ]
    if proof["lost"]:
        return "counterexample", bool(novel), proof
    if not novel:
        return "repeated evidence", False, proof
    if proof["gained"]:
        return "supported", True, proof
    return "no difference", True, proof


def observe(db, row, result):
    # Historical replays and other sessions cannot enroll themselves retrospectively.
    trial = available(db, search_trials.project_choice(db, row["project"])["id"])
    if not trial or row["id"] <= trial["cutoff"]:
        return
    stamps = result.get("field_checks", [])
    expected = dict(
        choice=trial["choice"],
        arm=trial["arm"],
        version=trial["version"],
        collection=result["collection"],
    )
    if (
        not stamps
        or len(stamps) != len(result["queries"])
        or any(s != expected for s in stamps)
    ):
        kind, eligible, proof = (
            "unverified",
            False,
            dict(
                reason="The original searches did not all carry this setting and collection receipt.",
                source_keys=sorted(set().union(*(keys(c) for c in result["cells"]))),
            ),
        )
    else:
        seen = set(json.loads(trial["source_keys"]))
        for (prior,) in db.execute(
            "SELECT evidence FROM search_choice_observations WHERE choice=?",
            (trial["choice"],),
        ):
            seen.update(json.loads(prior)["source_keys"])
        kind, eligible, proof = grade(result, trial["arm"], seen)
    db.execute(
        "INSERT OR IGNORE INTO search_choice_observations VALUES(?,?,?,?,?)",
        (trial["choice"], row["id"], kind, int(eligible), json.dumps(proof)),
    )


def history(db, project):
    initialize(db)
    active = search_trials.project_choice(db, project)["id"]
    out = []
    for f in db.execute(
        """SELECT f.*,b.title FROM search_choice_followups f
        JOIN research_assist_exports a ON a.id=f.origin JOIN briefs b ON b.id=a.brief
        WHERE f.project=? ORDER BY f.choice DESC LIMIT 3""",
        (project,),
    ):
        rows = db.execute(
            """SELECT o.job,o.kind,o.eligible,o.evidence,b.title FROM search_choice_observations o
            JOIN research_assist_exports a ON a.id=o.job JOIN briefs b ON b.id=a.brief
            WHERE o.choice=? ORDER BY o.job""",
            (f["choice"],),
        ).fetchall()
        eligible = sum(r["eligible"] for r in rows)
        state = (
            "superseded"
            if f["choice"] != active or f["version"] != search_focus.VERSION
            else "complete"
            if eligible >= EXPORT_LIMIT or len(rows) >= ATTEMPT_LIMIT
            else "observing"
        )
        out.append(
            dict(
                choice=f["choice"],
                arm=f["arm"],
                origin=f["title"],
                origin_job=f["origin"],
                state=state,
                eligible=eligible,
                limit=EXPORT_LIMIT,
                attempt_limit=ATTEMPT_LIMIT,
                observations=[
                    dict(
                        job=r["job"],
                        title=r["title"],
                        kind=r["kind"],
                        eligible=bool(r["eligible"]),
                        **{
                            k: v
                            for k, v in json.loads(r["evidence"]).items()
                            if k != "source_keys"
                        },
                    )
                    for r in rows
                ],
            )
        )
    return out
