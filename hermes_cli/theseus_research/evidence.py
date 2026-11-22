"""Versioned claim wording, exact passages and independent judgments about support."""

import hashlib
import json
from pathlib import Path
from hermes_constants import get_hermes_home
from . import briefs
from .refresh import REVIEW_SCHEMA, history_page
from .library import field, accessible_edition

DDL = """CREATE TABLE IF NOT EXISTS research_claim_reviews(id INTEGER PRIMARY KEY,brief INTEGER NOT NULL,revision INTEGER NOT NULL,
 row_name TEXT NOT NULL,col_name TEXT NOT NULL,layer TEXT NOT NULL,verdict TEXT NOT NULL,reason TEXT NOT NULL,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(brief,revision,row_name,col_name,layer,verdict,reason))"""
LAYERS = ("Observation", "Comparison", "Conclusion")
VERDICTS = ("Supports", "Insufficient", "Contradicts")


def archived(db, project, cell):
    if not cell.get("edition"):
        return None
    s = accessible_edition(db, project, cell["source"], cell["edition"])
    quote = cell.get("quote", "")
    offset = s["body"].find(quote) if quote else -1
    return dict(
        title=s["title"],
        edition=s["edition"],
        source=s["source"],
        origin_project=s["origin_project"],
        quote=quote,
        located=offset >= 0,
        offset=offset,
        context=s["body"][max(0, offset - 120) : offset + len(quote) + 120]
        if offset >= 0
        else "",
    )


def export_status(brief, revision, doc):
    p = (
        Path(get_hermes_home())
        / "research"
        / "exports"
        / f"brief-{brief}-r{revision}.csv"
    )
    if not p.is_file():
        return {"status": "Not exported"}
    if p.stat().st_size > 16_000_000:
        return {"status": "File differs"}
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    expected = hashlib.sha256(briefs.csv_text(doc).encode()).hexdigest()
    return dict(
        status="Verified export" if actual == expected else "File differs",
        digest=actual,
        path=str(p) if actual == expected else None,
    )


def operate(db, project, args):
    db.executescript(briefs.DDL + REVIEW_SCHEMA)
    db.execute(DDL)
    latest = briefs.get(db, project, args.get("brief"))
    row_name = field(args, "row", 160)
    col = field(args, "column", 100)
    if args["action"] == "evidence_review":
        layer = args.get("layer")
        verdict = args.get("verdict")
        rev = args.get("revision")
        if type(rev) != int or layer not in LAYERS or verdict not in VERDICTS:
            raise ValueError(
                "Choose a saved revision, claim layer and support judgment."
            )
        target = briefs.get(db, project, latest["brief"], rev)
        cell = next(
            (r["cells"].get(col) for r in target["rows"] if r["name"] == row_name), None
        )
        if not cell or not cell.get("value"):
            raise ValueError("Choose an existing claim to review.")
        if verdict in ("Supports", "Contradicts") and not (
            archived(db, project, cell) or {}
        ).get("located"):
            raise ValueError(
                "A supporting or contradicting judgment needs a located original passage."
            )
        db.execute(
            "INSERT OR IGNORE INTO research_claim_reviews(brief,revision,row_name,col_name,layer,verdict,reason) VALUES(?,?,?,?,?,?,?)",
            (
                latest["brief"],
                rev,
                row_name,
                col,
                layer,
                verdict,
                field(args, "reason", 1200),
            ),
        )
    elif args["action"] != "evidence_history":
        raise ValueError("Unknown evidence operation.")
    versions = []
    start = args.get("before", latest["latest"] + 1)
    if type(start) != int or start < 1:
        raise ValueError("Use a positive revision boundary.")
    history = db.execute(
        "SELECT revision,payload,created FROM brief_versions WHERE brief=? AND revision<? ORDER BY revision DESC LIMIT 30",
        (latest["brief"], start),
    ).fetchall()
    for r in history:
        doc = json.loads(r["payload"])
        cell = next(
            (row["cells"].get(col) for row in doc["rows"] if row["name"] == row_name),
            None,
        )
        if not cell:
            continue
        reviews = [
            dict(x)
            for x in db.execute(
                """SELECT layer,verdict,reason,created FROM research_claim_reviews
           WHERE brief=? AND revision=? AND row_name=? AND col_name=? ORDER BY id DESC""",
                (latest["brief"], r["revision"], row_name, col),
            )
        ]
        versions.append(
            dict(
                revision=r["revision"],
                created=r["created"],
                wording=cell["value"],
                note=cell.get("note", ""),
                evidence=archived(db, project, cell),
                export=export_status(latest["brief"], r["revision"], doc),
                reviews=reviews,
            )
        )
    return dict(
        citation_decisions=history_page(db, latest["brief"], row_name, col),
        versions=versions,
        next_before=history[-1]["revision"]
        if history and history[-1]["revision"] > 1
        else None,
    )
