"""Local research checkpoints, verified receipts, and question-directed frontier order."""

import hashlib
import re
from urllib.parse import urlsplit
from .library import field

DDL = """CREATE TABLE IF NOT EXISTS crawl_checkpoints(id INTEGER PRIMARY KEY,crawl INTEGER NOT NULL,question TEXT NOT NULL,
 note TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS crawl_attempts(id INTEGER PRIMARY KEY,crawl INTEGER NOT NULL,url TEXT NOT NULL,lease TEXT NOT NULL UNIQUE,
 started TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,finished TEXT,outcome TEXT);
CREATE TABLE IF NOT EXISTS crawl_edges(crawl INTEGER NOT NULL,parent TEXT NOT NULL,child TEXT NOT NULL,PRIMARY KEY(crawl,parent,child));"""
STOP = set(
    "what which where who when how why does did for the and are can this with that have from about tell look check there most only page pages web site".split()
)


def terms(text):
    return {
        w.rstrip("s")
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) > 2 and w not in STOP
    }


def queue(db, run, question):
    rows = db.execute(
        "SELECT * FROM crawl_pages WHERE crawl=? AND state='queued' ORDER BY id", (run,)
    ).fetchall()
    want = terms(question)

    def rank(row):
        tokens = terms(urlsplit(row["url"]).path)
        return sorted(want & tokens)

    return sorted(
        [(row, rank(row)) for row in rows],
        key=lambda v: (-len(v[1]), v[0]["depth"], v[0]["id"]),
    )


def question(db, run, original):
    r = db.execute(
        "SELECT question FROM crawl_checkpoints WHERE crawl=? ORDER BY id DESC LIMIT 1",
        (run,),
    ).fetchone()
    return r[0] if r else original


def summarize(db, project, run):
    job = db.execute(
        "SELECT * FROM crawls WHERE project=? AND id=?", (project, run)
    ).fetchone()
    if not job:
        raise ValueError("No such crawl in this project.")
    checkpoints = [
        dict(r)
        for r in db.execute(
            "SELECT question,note,created FROM crawl_checkpoints WHERE crawl=? ORDER BY id DESC LIMIT 50",
            (run,),
        )
    ]
    current = question(db, run, job["question"])
    pending = queue(db, run, current)
    checked = []
    for r in db.execute(
        """SELECT p.url,p.source,p.edition,p.digest,p.state,s.project,e.body,e.source AS owner,e.digest AS edigest
        FROM crawl_pages p LEFT JOIN editions e ON e.id=p.edition LEFT JOIN sources s ON s.id=e.source
        WHERE p.crawl=? AND p.state IN ('saved','duplicate') ORDER BY p.id""",
        (run,),
    ):
        ok = (
            r["project"] == project
            and r["owner"] == r["source"]
            and r["body"] is not None
            and hashlib.sha256(r["body"].encode()).hexdigest()
            == r["edigest"]
            == r["digest"]
        )
        checked.append(
            dict(
                url=r["url"],
                edition=r["edition"],
                digest=r["digest"],
                verified=ok,
                state=r["state"],
            )
        )
    next_page = None
    if pending:
        page, matched = pending[0]
        parent = db.execute(
            "SELECT parent FROM crawl_edges WHERE crawl=? AND child=? ORDER BY parent LIMIT 1",
            (run, page["url"]),
        ).fetchone()
        next_page = dict(
            url=page["url"],
            reason="Question terms in this URL: " + ", ".join(matched)
            if matched
            else "Shallowest remaining page, in discovery order.",
            parent=parent[0] if parent else None,
        )
    return dict(
        question=current,
        checkpoints=checkpoints,
        checked=checked,
        next=next_page,
        remaining=len(pending),
        domains=sorted({urlsplit(p["url"]).hostname for p, _ in pending}),
        attempts=[
            dict(r)
            for r in db.execute(
                "SELECT url,started,finished,outcome FROM crawl_attempts WHERE crawl=? ORDER BY id",
                (run,),
            )
        ],
    )


def checkpoint(db, project, args):
    if not db.execute(
        "SELECT 1 FROM crawls WHERE id=? AND project=?", (args.get("crawl"), project)
    ).fetchone():
        raise ValueError("No such crawl in this project.")
    q = field(args, "question", 500)
    note = field(args, "note", 1000, False)
    run = args["crawl"]
    last = db.execute(
        "SELECT question,note FROM crawl_checkpoints WHERE crawl=? ORDER BY id DESC LIMIT 1",
        (run,),
    ).fetchone()
    if not last or tuple(last) != (q, note):
        db.execute(
            "INSERT INTO crawl_checkpoints(crawl,question,note) VALUES(?,?,?)",
            (run, q, note),
        )
    return summarize(db, project, run)
