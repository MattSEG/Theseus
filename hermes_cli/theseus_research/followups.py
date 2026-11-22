"""Prospective checks of a laboratory result against a later brief export."""

import csv
import hashlib
import io
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from hermes_constants import get_hermes_home
from . import briefs, pilot_policy
from .library import accessible_edition, field

KINDS = ("revision", "condition", "conflict")
TABLE = """CREATE TABLE IF NOT EXISTS research_predictions(
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
 row_name TEXT NOT NULL, col TEXT NOT NULL, kind TEXT NOT NULL, run INTEGER NOT NULL,
 policy INTEGER NOT NULL, benchmark TEXT NOT NULL, edition INTEGER NOT NULL,
 source_digest TEXT NOT NULL, quote TEXT NOT NULL, expected TEXT NOT NULL,
 rehearsal INTEGER NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 resolved_seq INTEGER UNIQUE, outcome TEXT, receipt TEXT, UNIQUE(project,title,row_name,col))"""


def forecasts(order, checks):
    """Publish testable field forecasts as part of the evaluation, before any keep decision."""
    result = {}
    for arm, cases in checks.items():
        by_kind = {}
        for kind, case in zip(order, cases, strict=True):
            values = [project["value"] for project in case["projects"].values()]
            by_kind[kind] = {
                "prediction": "correct literal value in next enrolled export"
                if values and all(values)
                else "abstain",
                "correct": sum(values),
                "tested": len(values),
            }
        result[arm] = by_kind
    return result


def latest_benchmark(kind):
    from . import pilot

    root = pilot.workspace()
    key = pilot.latest(root)
    if not key:
        raise ValueError("Finish a comparison experiment first.")
    folder = root / key
    state = pilot.read(folder / "state.json")
    if state["status"] != "complete" or "incumbent" not in state:
        raise ValueError("Choose a finished comparison with a recorded baseline.")
    run = state["run"]
    files = json.loads(pilot.journal("check-files", run))["files"]
    if not any(
        Path(f["path"]) == folder / "comparison.json" and f["state"] == "current"
        for f in files
    ):
        raise ValueError("The experiment report could not be verified.")
    report = pilot.read(folder / "comparison.json")
    active = pilot_policy.current(root.parent.parent)
    arm = "current"
    if active["id"] != state["incumbent"]:
        winner = report["result"]["winner"]
        if (
            active.get("action") != "apply"
            or active.get("run") != run
            or winner == "current"
            or report["result"]["tied_candidates"]
        ):
            raise ValueError("Test the active comparison instructions first.")
        arm = winner
    series = report.get("field_forecasts")
    if series is None:
        series = forecasts(report["test_order"], report["checks_by_arm"])
    forecast = series[arm][kind]
    if forecast["prediction"] == "abstain":
        raise ValueError(
            "The tested instructions missed values on this condition. They do not support a positive forecast."
        )
    return (
        run,
        active["id"],
        {
            "arm": arm,
            "values_correct": forecast["correct"],
            "forecast_origin": "experiment report"
            if "field_forecasts" in report
            else "reconstructed from a prior benchmark before enrollment",
            "model": report["model"],
            "report_sha256": hashlib.sha256(
                (folder / "comparison.json").read_bytes()
            ).hexdigest(),
        },
    )


def reviewed_examples(db, project):
    from . import evidence

    db.execute(evidence.DDL)
    records = db.execute(
        """SELECT r.*, b.title FROM research_claim_reviews r JOIN briefs b ON b.id=r.brief
                         WHERE b.project=? ORDER BY r.id DESC LIMIT 12""",
        (project,),
    ).fetchall()
    examples = []
    for record in records:
        doc = briefs.get(db, project, record["brief"], record["revision"])
        status = evidence.export_status(record["brief"], record["revision"], doc)
        if status["status"] != "Verified export":
            continue
        row = next(
            (row for row in doc["rows"] if row["name"] == record["row_name"]), {}
        )
        cell = row.get("cells", {}).get(record["col_name"], {})
        try:
            original = evidence.archived(db, project, cell)
        except ValueError:
            continue
        if original and original["located"]:
            examples.append(
                dict(
                    review=record["id"],
                    question=doc["question"],
                    title=record["title"],
                    row=record["row_name"],
                    column=record["col_name"],
                    brief=record["brief"],
                    revision=record["revision"],
                    verdict=record["verdict"],
                    reason=record["reason"],
                    edition=original["edition"],
                    quote=original["quote"],
                    value=cell.get("value"),
                    digest=status["digest"],
                )
            )
    return examples


def export_matches(home, proof):
    file = (
        home / "research/exports" / f"brief-{proof['brief']}-r{proof['revision']}.csv"
    )
    try:
        return (
            str(file) == proof["path"]
            and file.stat().st_size <= 16_000_000
            and hashlib.sha256(file.read_bytes()).hexdigest() == proof["sha256"]
        )
    except OSError:
        return False


def display_receipt(raw):
    if not raw:
        return None
    proof = json.loads(raw)
    return {**proof, "file_current": export_matches(Path(get_hermes_home()), proof)}


def operate(db, project, args):
    db.executescript(briefs.DDL)
    db.execute(TABLE)
    if args["action"] == "prediction_freeze":
        kind = args.get("kind")
        if kind not in KINDS:
            raise ValueError("Choose a tested condition.")
        run, policy, benchmark = latest_benchmark(kind)
        title = field(args, "title", 240)
        row = field(args, "row", 160)
        column = field(args, "column", 100)
        expected = field(args, "expected", 400)
        quote = field(args, "quote", 2400)
        db.execute("BEGIN IMMEDIATE")
        source = accessible_edition(db, project, edition=args.get("edition"))
        if (
            accessible_edition(db, project, source=source["source"])["edition"]
            != source["edition"]
        ):
            raise ValueError("Use the current governing edition in this project.")
        if quote not in source["body"] or expected not in quote:
            raise ValueError(
                "Select an exact passage containing the expected cell value."
            )
        if db.execute(
            "SELECT 1 FROM briefs WHERE project=? AND title=?", (project, title)
        ).fetchone():
            raise ValueError("Freeze the forecast before anyone creates the brief.")
        try:
            db.execute(
                """INSERT INTO research_predictions(project,title,row_name,col,kind,run,policy,benchmark,
                        edition,source_digest,quote,expected,rehearsal) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    project,
                    title,
                    row,
                    column,
                    kind,
                    run,
                    policy,
                    json.dumps(benchmark),
                    source["edition"],
                    source["digest"],
                    quote,
                    expected,
                    int(args.get("rehearsal") is True),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "This future cell already has a frozen prediction."
            ) from error
    elif args["action"] != "prediction_list":
        raise ValueError("Unknown prediction action.")
    rows = db.execute(
        """SELECT id,title,row_name,col,kind,run,expected,rehearsal,outcome,receipt,benchmark
                      FROM research_predictions WHERE project=? ORDER BY id DESC LIMIT 50""",
        (project,),
    )
    return {
        "examples": reviewed_examples(db, project),
        "predictions": [
            {
                **dict(r),
                "receipt": display_receipt(r["receipt"]),
                "benchmark": json.loads(r["benchmark"]),
            }
            for r in rows
        ],
    }


def observe(db, doc, path):
    """Called by ordinary exports. An exact title binds the first export, including missing cells."""
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='research_predictions'"
    ).fetchone():
        return
    watches = db.execute(
        """SELECT * FROM research_predictions WHERE project=? AND title=? AND outcome IS NULL""",
        (doc["project"], doc["title"]),
    ).fetchall()
    if not watches:
        return
    previous_export = any(
        p != Path(path) for p in Path(path).parent.glob(f"brief-{doc['brief']}-r*.csv")
    )
    raw = Path(path).read_bytes()
    exact = raw == briefs.csv_text(doc).encode()
    data = list(csv.reader(io.StringIO(raw.decode()))) if exact else []
    policy = pilot_policy.current(Path(get_hermes_home()))["id"]
    for w in watches:
        try:
            source = accessible_edition(db, doc["project"], edition=w["edition"])
            current = accessible_edition(db, doc["project"], source=source["source"])
        except ValueError:
            source, current = None, None
        outcome, actual = "Inconclusive", None
        if previous_export:
            reason = "An earlier export exists without a recorded first observation."
        elif not exact:
            reason = "Export bytes differ from the saved revision."
        elif (
            not current
            or current["edition"] != w["edition"]
            or hashlib.sha256(source["body"].encode()).hexdigest() != w["source_digest"]
        ):
            reason = "The governing source changed after registration."
        elif policy != w["policy"]:
            reason = "Comparison instructions changed after registration."
        else:
            # Locate by unescaped logical names; formula-safe CSV headers need not be unique.
            index = next(
                (i for i, r in enumerate(doc["rows"], 1) if r["name"] == w["row_name"]),
                None,
            )
            if index is not None and doc["rows"][index - 1]["cells"].get(
                w["col"], {}
            ).get("value"):
                actual = data[index][1 + 3 * doc["columns"].index(w["col"])]
            outcome = (
                "Confirmed"
                if actual == briefs.safe_cell(w["expected"])
                else "Contradicted"
            )
            reason = (
                "Compared the literal saved cell with the preselected source value."
            )
        receipt = dict(
            brief=doc["brief"],
            revision=doc["revision"],
            path=str(path),
            actual=actual,
            sha256=hashlib.sha256(raw).hexdigest(),
            reason=reason,
            edition=w["edition"],
        )
        db.execute(
            "UPDATE research_predictions SET resolved_seq=(SELECT coalesce(max(resolved_seq),0)+1 FROM research_predictions),outcome=?,receipt=? WHERE id=? AND outcome IS NULL",
            (outcome, json.dumps(receipt), w["id"]),
        )


def verified_observations(home, clause, params):
    path = home / "research/library.sqlite"
    if not path.is_file():
        return
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='research_predictions'"
        ).fetchone():
            return
        records = db.execute(
            f"""SELECT w.*,e.body FROM research_predictions w
            JOIN editions e ON e.id=w.edition WHERE w.rehearsal=0 AND w.outcome IS NOT NULL
            AND {clause} ORDER BY w.resolved_seq""",
            params,
        )
        for r in records:
            proof = json.loads(r["receipt"])
            if (
                not export_matches(home, proof)
                or hashlib.sha256(r["body"].encode()).hexdigest() != r["source_digest"]
                or r["quote"] not in r["body"]
            ):
                continue
            yield r


def next_counterexample(home, after):
    """Project details and answer keys stay out of shared proposal history."""
    rows = verified_observations(
        home, "w.resolved_seq>? AND w.outcome='Contradicted'", (after,)
    )
    with closing(rows):
        row = next(rows, None)
    return (
        dict(sequence=row["resolved_seq"], run=row["run"], kind=row["kind"])
        if row
        else None
    )


def calibration(home, runs):
    """Counts describe prospective artifact checks, without attributing causation to a model."""
    counts = {}
    if runs:
        marks = ",".join("?" for _ in runs)
        for row in verified_observations(home, f"w.run IN ({marks})", tuple(runs)):
            totals = counts.setdefault(str(row["run"]), {}).setdefault(row["kind"], {})
            totals[row["outcome"]] = totals.get(row["outcome"], 0) + 1
    return counts
