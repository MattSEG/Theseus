"""Manually reviewed decision records with retained rationale and trigger history."""

import json
from datetime import date
from . import briefs
from .library import field

DDL = """CREATE TABLE IF NOT EXISTS planning_decisions(id INTEGER PRIMARY KEY,project TEXT NOT NULL,title TEXT NOT NULL,
 revision INTEGER NOT NULL,state TEXT NOT NULL,due TEXT NOT NULL,event TEXT NOT NULL, UNIQUE(project,title));
CREATE TABLE IF NOT EXISTS planning_decision_versions(decision INTEGER NOT NULL,revision INTEGER NOT NULL,
 data TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(decision,revision));"""


def day(value):
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("Enter the date as YYYY-MM-DD.") from None
    if parsed.isoformat() != value:
        raise ValueError("Enter the date as YYYY-MM-DD.")
    return value


def operate(db, project, args):
    db.executescript(briefs.DDL + DDL)
    action = args["action"]
    if action != "decision_list":
        db.execute("BEGIN IMMEDIATE")
        if action == "decision_save":
            title = field(args, "title", 200)
            decision = field(args, "choice", 1000)
            reason = field(args, "reason", 1200)
            assumption = field(args, "assumption", 800)
            due = day(args.get("due"))
            event = field(args, "event", 800, False)
            if not (due or event):
                raise ValueError("Set a review date or a triggering event.")
            doc = dict(
                choice=decision,
                reason=reason,
                assumption=assumption,
                due=due,
                event=event,
                action="Recorded",
            )
            if args.get("brief"):
                source = briefs.get(db, project, args["brief"])
                doc.update(brief=source["brief"], brief_revision=source["revision"])
            bid = db.execute(
                "INSERT INTO planning_decisions(project,title,revision,state,due,event) VALUES(?,?,1,'Watching',?,?)",
                (project, title, due, event),
            ).lastrowid
            rev = 1
        else:
            row = db.execute(
                """SELECT d.*,v.data FROM planning_decisions d JOIN planning_decision_versions v ON v.decision=d.id
                AND v.revision=d.revision WHERE project=? AND d.id=?""",
                (project, args.get("decision")),
            ).fetchone()
            if not row or row["revision"] != args.get("revision"):
                raise ValueError("Reload the decision before recording a change.")
            bid = row["id"]
            rev = row["revision"] + 1
            doc = json.loads(row["data"])
            doc["review_note"] = field(args, "note", 1200)
            due = row["due"]
            if action == "decision_trigger":
                if row["state"] != "Watching":
                    raise ValueError(
                        "Only a watched decision can be flagged for review."
                    )
                state = "Triggered"
                doc["action"] = "Event reported"
            elif action == "decision_review":
                if row["state"] == "Reviewed":
                    raise ValueError("This review is already complete.")
                result = args.get("outcome")
                if result not in ("Kept", "Revised", "Deferred"):
                    raise ValueError("Choose Kept, Revised or Deferred.")
                if result == "Revised":
                    doc["choice"] = field(args, "choice", 1000)
                    doc["reason"] = doc["review_note"]
                due = day(args.get("due"))
                if result == "Deferred" and (
                    not due or due <= date.today().isoformat()
                ):
                    raise ValueError("Deferring a review needs a future date.")
                doc["action"] = result
                state = "Watching" if due else "Reviewed"
                doc["due"] = due
            else:
                raise ValueError("Unknown decision operation.")
            db.execute(
                "UPDATE planning_decisions SET state=?,due=?,revision=? WHERE id=?",
                (state, due, rev, bid),
            )
        db.execute(
            "INSERT INTO planning_decision_versions(decision,revision,data) VALUES(?,?,?)",
            (bid, rev, json.dumps(doc)),
        )
    out = []
    today = date.today().isoformat()
    for r in db.execute(
        "SELECT * FROM planning_decisions WHERE project=? OR ? ORDER BY id DESC LIMIT 180",
        (project, args.get("all_projects") is True),
    ):
        item = dict(r)
        item["status"] = (
            "Due"
            if r["state"] == "Watching" and r["due"] and r["due"] <= today
            else r["state"]
        )
        item["history"] = [
            dict(created=v["created"], revision=v["revision"], **json.loads(v["data"]))
            for v in db.execute(
                "SELECT * FROM planning_decision_versions WHERE decision=? ORDER BY revision DESC",
                (r["id"],),
            )
        ]
        out.append(item)
    return dict(decisions=out, today=today)
