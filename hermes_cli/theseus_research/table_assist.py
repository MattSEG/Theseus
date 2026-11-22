"""Suggest descriptive ranges for a small table with an explicit measure and grouping."""

import hashlib
import re
from collections import Counter
from pathlib import Path

from . import analysis, library, measurement_stats

MAX_BYTES = 120_000
MAX_ROWS = 1000
UNIT = re.compile(r"^(.+?)\s*[\[(]([^\[\]()]{1,18})[\])]$")
UNITS = {
    "s",
    "sec",
    "ms",
    "min",
    "minutes",
    "h",
    "hr",
    "hours",
    "d",
    "days",
    "g",
    "kg",
    "mg",
    "µg",
    "L",
    "mL",
    "mm",
    "cm",
    "m",
    "km",
    "in",
    "ft",
    "°C",
    "°F",
    "K",
    "Hz",
    "kHz",
    "V",
    "mV",
    "A",
    "mA",
    "W",
    "kW",
    "kWh",
    "N",
    "Pa",
    "kPa",
    "MPa",
    "%",
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "USD/unit",
    "mg/L",
    "m/s",
}
IDENTIFIER = re.compile(
    r"(?:^|\b)(?:id|identifier|code|zip|postal|account|serial|phone|invoice number|order number)\b",
    re.I,
)
DDL = "CREATE TABLE IF NOT EXISTS table_analysis_settings(project TEXT PRIMARY KEY,enabled INTEGER NOT NULL)"


def inspect_table(body, digest=""):
    if len(body) > MAX_BYTES or len(body.encode("utf-8")) > MAX_BYTES:
        return None
    try:
        header, rows = measurement_stats.records(body)
    except ValueError:
        return None
    if not 4 <= len(rows) <= MAX_ROWS or not 2 <= len(header) <= 24:
        return None
    measures = []
    for i, label in enumerate(header):
        match = UNIT.fullmatch(label)
        if match and match[2] in UNITS and not IDENTIFIER.search(match[1]):
            measures.append((i, label, match[2]))
    if len(measures) != 1:
        return None
    pos, measure, unit = measures[0]
    groups = []
    for i, label in enumerate(header):
        if i == pos or IDENTIFIER.search(label):
            continue
        values = [r[i].strip() for r in rows if len(r) == len(header)]
        if label.strip().casefold() in {"unit", "units", "currency", "denomination"}:
            if not values or any(v != unit for v in values):
                return None
            continue
        counts = Counter(v for v in values if v)
        if (
            2 <= len(counts) <= 8
            and all(n >= 2 for n in counts.values())
            and all(len(v) <= 100 and any(c.isalpha() for c in v) for v in counts)
        ):
            groups.append(label)
    if len(groups) != 1:
        return None
    # Use the analysis engine's own rules for accepted and excluded records.
    params = dict(measure=measure, group=groups[0], unit=unit)
    try:
        report = measurement_stats.summarize(
            dict(body=body, digest=digest), params, parsed_table=(header, rows)
        )
    except (ValueError, ArithmeticError):
        return None
    if (
        len(report["groups"]) < 2
        or any(g["n"] < 2 for g in report["groups"])
        or 2 * report["excluded_count"] > len(rows)
    ):
        return None
    return dict(**params, records=len(rows), excluded=report["excluded_count"]), report


def candidate(body):
    checked = inspect_table(body)
    return checked[0] if checked else None


def enabled(db, project):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='table_analysis_settings'"
    ).fetchone():
        return True
    row = db.execute(
        "SELECT enabled FROM table_analysis_settings WHERE project=?", (project,)
    ).fetchone()
    return bool(row[0]) if row else True


def offer(db, project, result, origin, body):
    if Path(origin).suffix.lower() not in (".csv", ".tsv") or not enabled(db, project):
        return
    params = candidate(body)
    if params:
        result["analysis_suggestion"] = dict(
            source=result["source"],
            edition=result["edition"],
            sha256=result["digest"],
            preview_action="analysis_preview",
            **params,
        )


def operate(db, project, args):
    if args["action"] == "analysis_suggestions":
        db.execute(DDL)
        if "enabled" in args:
            db.execute("BEGIN IMMEDIATE")
            value = args["enabled"]
            if type(value) is not bool or args.get("expected") is not enabled(
                db, project
            ):
                raise ValueError(
                    "Refresh the project before changing its analysis suggestions."
                )
            db.execute(
                "INSERT OR REPLACE INTO table_analysis_settings VALUES(?,?)",
                (project, int(value)),
            )
        return dict(enabled=enabled(db, project))
    save = args["action"] == "analysis_keep_preview"
    db.execute("BEGIN IMMEDIATE" if save else "BEGIN")
    source = library.accessible_edition(db, project, source=args.get("source"))
    if (
        source["edition"] != args.get("edition")
        or source["digest"] != args.get("sha256")
        or hashlib.sha256(source["body"].encode()).hexdigest() != source["digest"]
    ):
        raise ValueError("The selected table changed. Review its current edition.")
    checked = inspect_table(source["body"], source["digest"])
    if not checked:
        raise ValueError("There is no supported range preview for this table.")
    hint, report = checked
    params = {k: hint[k] for k in ("measure", "group", "unit")}
    if save:
        title = f"{params['measure']} by {params['group']}"
        run = analysis.store_run(db, project, source, title, params, report)
        return dict(run=run, title=title, edition=source["edition"])
    return dict(
        source=source["source"],
        edition=source["edition"],
        sha256=source["digest"],
        **params,
        records=report["records"],
        excluded_count=report["excluded_count"],
        excluded=report["excluded"],
        groups=[
            {
                k: g[k]
                for k in (
                    "group",
                    "n",
                    "minimum",
                    "maximum",
                    "min_record",
                    "max_record",
                )
            }
            for g in report["groups"]
        ],
    )
