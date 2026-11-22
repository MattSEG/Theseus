"""Matched categorical comparisons using frozen expectations and physical output files."""

import csv
import hashlib
import io
import json
from pathlib import Path
from hermes_constants import get_hermes_home
from . import briefs
from .library import accessible_edition, field

KINDS = ("positive", "ambiguous", "missing")
DDL = """CREATE TABLE IF NOT EXISTS paired_suites(id INTEGER PRIMARY KEY,project TEXT NOT NULL,title TEXT NOT NULL,
 question TEXT NOT NULL, col TEXT NOT NULL,cases TEXT NOT NULL,cutoff INTEGER NOT NULL,UNIQUE(project,title));
CREATE TABLE IF NOT EXISTS paired_results(id INTEGER PRIMARY KEY,suite INTEGER NOT NULL,report TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(suite,report));"""


def freeze(db, project, args):
    roster = args.get("cases")
    if not isinstance(roster, list) or len(roster) != 3:
        raise ValueError("Register positive, ambiguous and missing conditions.")
    cases = []
    for c in roster:
        if not isinstance(c, dict) or c.get("kind") not in KINDS:
            raise ValueError("Choose a supported condition.")
        s = accessible_edition(db, project, edition=c.get("edition"))
        quote = field(c, "quote", 2400)
        if quote not in s["body"]:
            raise ValueError(
                "Each reference needs a passage actually present in its archived edition."
            )
        cases.append(
            dict(
                kind=c["kind"],
                row=field(c, "row", 160),
                expected=field(c, "expected", 400),
                source=s["source"],
                edition=s["edition"],
                digest=s["digest"],
                quote=quote,
            )
        )
    if any(len({c[k] for c in cases}) != 3 for k in ("kind", "source", "row")):
        raise ValueError(
            "Use three distinct conditions, row names and original sources."
        )
    if len({c["expected"].strip().casefold() for c in cases}) < 2:
        raise ValueError("At least one observation must change the expected answer.")
    return cases


def artifact(db, project, ref, column, cases):
    try:
        bid, rev = map(int, ref.split(":"))
    except (ValueError, AttributeError):
        raise ValueError("Select an exported brief as ID:revision.") from None
    doc = briefs.get(db, project, bid, rev)
    if column not in doc["columns"]:
        raise ValueError("Both exports need the registered answer column.")
    path = Path(get_hermes_home()) / "research" / "exports" / f"brief-{bid}-r{rev}.csv"
    if not path.is_file() or path.stat().st_size > 16_000_000:
        raise ValueError("Export this revision to CSV before scoring.")
    raw = path.read_bytes()
    if raw != briefs.csv_text(doc).encode():
        raise ValueError("The CSV differs from the saved brief revision.")
    rows = {r["Item"]: r for r in csv.DictReader(io.StringIO(raw.decode()))}
    docs = {r["name"]: r for r in doc["rows"]}
    scores = []
    for case in cases:
        name = case["row"]
        actual = rows.get(briefs.safe_cell(name), {}).get(briefs.safe_cell(column))
        explicit = bool(
            docs.get(name, {}).get("cells", {}).get(column, {}).get("value", "").strip()
        )
        passed = (
            explicit
            and actual is not None
            and " ".join(actual.casefold().split())
            == " ".join(briefs.safe_cell(case["expected"]).casefold().split())
        )
        scores.append(
            dict(
                kind=case["kind"],
                row=name,
                actual=actual,
                passed=passed,
                explicit=explicit,
            )
        )
    seq = db.execute(
        "SELECT rowid FROM brief_versions WHERE brief=? AND revision=?", (bid, rev)
    ).fetchone()[0]
    return dict(
        brief=bid,
        revision=rev,
        title=doc["title"],
        path=str(path),
        digest=hashlib.sha256(raw).hexdigest(),
        scores=scores,
    ), seq


def operate(db, project, args):
    db.executescript(briefs.DDL + DDL)
    action = args["action"]
    if action == "paired_freeze":
        db.execute("BEGIN IMMEDIATE")
        cases = freeze(db, project, args)
        cutoff = db.execute(
            "SELECT coalesce(max(rowid),0) FROM brief_versions"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO paired_suites(project,title,question,col,cases,cutoff) VALUES(?,?,?,?,?,?)",
            (
                project,
                field(args, "title", 180),
                field(args, "question", 1000),
                field(args, "column", 100),
                json.dumps(cases),
                cutoff,
            ),
        )
    elif action in ("paired_score", "paired_export"):
        suite = db.execute(
            "SELECT * FROM paired_suites WHERE id=? AND project=?",
            (args.get("suite"), project),
        ).fetchone()
        if not suite:
            raise ValueError("Choose a registered suite in this project.")
        if action == "paired_export":
            result = db.execute(
                "SELECT report FROM paired_results WHERE id=? AND suite=?",
                (args.get("result"), suite["id"]),
            ).fetchone()
            if not result:
                raise ValueError("Select an actual trial result.")
            return {
                "path": briefs.write_export(
                    f"paired-{suite['id']}-result-{int(args['result'])}.json",
                    json.dumps(json.loads(result[0]), indent=2),
                )
            }
        cases = json.loads(suite["cases"])
        for c in cases:
            s = accessible_edition(db, project, c["source"], c["edition"])
            if (
                hashlib.sha256(s["body"].encode()).hexdigest() != c["digest"]
                or c["quote"] not in s["body"]
            ):
                raise ValueError("A frozen source receipt no longer verifies.")
        base, _ = artifact(db, project, args.get("baseline"), suite["col"], cases)
        new, seq = artifact(db, project, args.get("candidate"), suite["col"], cases)
        if base["path"] == new["path"] or seq <= suite["cutoff"]:
            raise ValueError(
                "Choose distinct outputs and a candidate saved after the roster was frozen."
            )
        delta = [
            int(n["passed"]) - int(b["passed"])
            for b, n in zip(base["scores"], new["scores"])
        ]
        decision = (
            "Regression"
            if -1 in delta
            else ("Measured gain" if 1 in delta else "No measured gain")
        )
        report = dict(
            suite=suite["id"],
            question=suite["question"],
            column=suite["col"],
            cases=cases,
            baseline=base,
            candidate=new,
            decision=decision,
        )
        db.execute(
            "INSERT OR IGNORE INTO paired_results(suite,report) VALUES(?,?)",
            (suite["id"], json.dumps(report)),
        )
    elif action != "paired_list":
        raise ValueError("Unknown paired trial action.")
    suites = [
        dict(r)
        for r in db.execute(
            "SELECT id,title,question,col FROM paired_suites WHERE project=? ORDER BY id DESC",
            (project,),
        )
    ]
    for s in suites:
        s["results"] = [
            dict(id=r["id"], **json.loads(r["report"]))
            for r in db.execute(
                "SELECT id,report FROM paired_results WHERE suite=? ORDER BY id DESC LIMIT 15",
                (s["id"],),
            )
        ]
    return {"suites": suites}
