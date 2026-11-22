"""Versioned research briefs. A revision is one atomic set of user or model edits."""

import csv
import io
import json
import os
import tempfile
from pathlib import Path

from hermes_constants import get_hermes_home
from .library import field, accessible_edition

TEMPLATES = {
    "Market comparison": ["Offering", "Price", "Terms", "Open question"],
    "Scientific review": ["Method", "Endpoint", "Result", "Limitation"],
    "Personal decision": ["Cost", "Timing", "Fit", "Trade-off"],
}
DDL = """
CREATE TABLE IF NOT EXISTS briefs(id INTEGER PRIMARY KEY,project TEXT NOT NULL,title TEXT NOT NULL,
 template TEXT NOT NULL, revision INTEGER NOT NULL, UNIQUE(project,title));
CREATE TABLE IF NOT EXISTS brief_versions(brief INTEGER NOT NULL REFERENCES briefs(id), revision INTEGER NOT NULL,
 payload TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(brief,revision));
"""


def get(db, project, brief, revision=None):
    row = db.execute(
        """SELECT b.id AS brief,b.project,b.title,b.template,b.revision AS latest,
        v.revision,v.payload,v.created FROM briefs b JOIN brief_versions v ON b.id=v.brief
        WHERE b.project=? AND b.id=? AND v.revision=COALESCE(?,b.revision)""",
        (project, brief, revision),
    ).fetchone()
    if not row:
        raise ValueError("No matching brief revision in this project.")
    result = dict(row)
    result.update(json.loads(result.pop("payload")))
    return result


def columns_for(args):
    template = args.get("template", "Market comparison")
    if template not in TEMPLATES:
        raise ValueError("Choose a supported brief template.")
    cols = args.get("columns", TEMPLATES[template])
    if not isinstance(cols, list) or not 1 <= len(cols) <= 20:
        raise ValueError("Use 1 to 20 columns.")
    cols = [field({"column": name}, "column", 100) for name in cols]
    if len(set(cols)) != len(cols):
        raise ValueError("Use distinct column names.")
    return template, cols


def checked_cell(db, project, cell):
    if not isinstance(cell, dict):
        raise ValueError("A cell needs a value and optional citation.")
    value = field(cell, "value", 3000, False)
    note = field(cell, "note", 3000, False)
    out = {"value": value, "note": note}
    edition = cell.get("edition")
    quote = field(cell, "quote", 2400, False)
    if edition is not None or quote:
        row = accessible_edition(db, project, edition=edition)
        if not row or not quote or quote not in row["body"]:
            raise ValueError(
                "The quoted passage must occur in the selected edition in this project."
            )
        out.update(source=row["id"], edition=edition, quote=quote)
    return out


def apply_rows(db, project, doc, rows):
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("Use up to 100 rows per edit.")
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("Each row needs a name.")
        name = field(item, "name", 160)
        row = next((r for r in doc["rows"] if r["name"] == name), None)
        if row is None:
            row = {"name": name, "cells": {}}
            doc["rows"].append(row)
        cells = item.get("cells", {})
        if not isinstance(cells, dict) or not set(cells).issubset(doc["columns"]):
            raise ValueError("Use the brief's existing column names.")
        for col, cell in cells.items():
            updated = checked_cell(db, project, cell)
            if "note" not in cell:
                updated["note"] = row["cells"].get(col, {}).get("note", "")
            row["cells"][col] = updated
    if len(doc["rows"]) > 250:
        raise ValueError("A brief supports up to 250 rows.")


def store(db, brief, revision, doc):
    saved = {k: doc[k] for k in ("question", "columns", "rows")}
    payload = json.dumps(saved, ensure_ascii=False, allow_nan=False)
    if len(payload) > 2_000_000:
        raise ValueError("The brief is too large.")
    db.execute(
        "INSERT INTO brief_versions(brief,revision,payload) VALUES(?,?,?)",
        (brief, revision, payload),
    )
    db.execute("UPDATE briefs SET revision=? WHERE id=?", (revision, brief))
    from .source_impact import index
    index(db, brief, revision, saved["rows"])


def safe_cell(value):
    # Data exported as CSV should open as data even if a source starts with a spreadsheet formula.
    return (
        "'" + value
        if value.lstrip().startswith(("=", "+", "-", "@"))
        and not value.replace(".", "", 1).lstrip("-").isdigit()
        else value
    )


def write_export(name, text):
    root = Path(get_hermes_home()) / "research" / "exports"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = root / name
    with tempfile.NamedTemporaryFile(
        dir=root, delete=False, mode="w", encoding="utf-8", newline=""
    ) as tmp:
        try:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            Path(tmp.name).unlink(missing_ok=True)
            raise
    os.replace(tmp.name, target)
    return str(target)


def csv_text(doc):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    headers = ["Item"]
    for col in doc["columns"]:
        headers.extend([col, col + " evidence", col + " note"])
    writer.writerow(list(map(safe_cell, headers)))
    for row in doc["rows"]:
        values = [row["name"]]
        for col in doc["columns"]:
            cell = row["cells"].get(col, {})
            cite = (
                f"Source {cell['source']}, edition {cell['edition']}: {cell['quote']}"
                if cell.get("edition")
                else ""
            )
            values.extend(
                (cell.get("value") or "Not recorded", cite, cell.get("note", ""))
            )
        writer.writerow(list(map(safe_cell, values)))
    return out.getvalue()


def operate(db, project, args, *, export_session=None):
    db.executescript(DDL)
    action = args["action"]
    bid = args.get("brief")
    if action == "brief_list":
        return {
            "briefs": [
                dict(r)
                for r in db.execute(
                    "SELECT id AS brief,title,template,revision FROM briefs WHERE project=? ORDER BY id DESC",
                    (project,),
                )
            ],
            "templates": TEMPLATES,
        }
    if action == "brief_passage":
        doc = get(db, project, bid, args.get("revision"))
        row = next((r for r in doc["rows"] if r["name"] == args.get("row")), {})
        cell = row.get("cells", {}).get(args.get("column"), {})
        if not cell.get("edition"):
            raise ValueError("No source passage for this cell.")
        source = accessible_edition(db, project, cell["source"], cell["edition"])
        offset = source["body"].find(cell["quote"]) if source else -1
        if offset < 0:
            raise ValueError("The saved passage could not be verified.")
        start = max(0, offset - 200)
        end = offset + len(cell["quote"]) + 200
        return {
            "title": source["title"],
            "edition": cell["edition"],
            "offset": offset,
            "text": source["body"][start:end],
        }
    if action == "brief_read":
        return get(db, project, bid, args.get("revision"))
    if action == "brief_export":
        doc = get(db, project, bid, args.get("revision"))
        from .preferences import record_export

        db.execute("BEGIN IMMEDIATE")
        path = write_export(
            f"brief-{doc['brief']}-r{doc['revision']}.csv", csv_text(doc)
        )
        record_export(db, doc)
        from .followups import observe

        observe(db, doc, path)
        if export_session:
            from .assistance import enqueue
            enqueue(db, doc, export_session)
        return {"path": path, "revision": doc["revision"]}
    db.execute("BEGIN IMMEDIATE")
    if action == "brief_create":
        title = field(args, "title", 240)
        template, cols = columns_for(args)
        if "columns" not in args:
            from .preferences import default_columns

            cols = default_columns(db, project, template) or cols
        if db.execute(
            "SELECT id FROM briefs WHERE project=? AND title=?", (project, title)
        ).fetchone():
            raise ValueError("A brief with that title already exists. Open it to edit.")
        doc = {"question": field(args, "question", 1400), "columns": cols, "rows": []}
        bid = db.execute(
            "INSERT INTO briefs(project,title,template,revision) VALUES(?,?,?,0)",
            (project, title, template),
        ).lastrowid
        revision = 1
    elif action == "brief_edit":
        doc = get(db, project, bid)
        if (
            type(args.get("expected_revision")) is not int
            or args["expected_revision"] != doc["revision"]
        ):
            raise ValueError(
                "This brief changed. Reopen the latest revision before editing."
            )
        revision = doc["revision"] + 1
        if "add_column" in args:
            col = field(args, "add_column", 100)
            if col in doc["columns"] or len(doc["columns"]) >= 20:
                raise ValueError("Choose a new column name, up to 20 columns.")
            doc["columns"].append(col)
        if "rename_column" in args:
            old = field(args, "rename_column", 100)
            new = field(args, "new_name", 100)
            if old not in doc["columns"] or new in doc["columns"]:
                raise ValueError("Choose an existing column and a distinct new name.")
            doc["columns"][doc["columns"].index(old)] = new
            for row in doc["rows"]:
                if old in row["cells"]:
                    row["cells"][new] = row["cells"].pop(old)
    else:
        raise ValueError("Unknown brief operation.")
    apply_rows(db, project, doc, args.get("rows", []))
    store(db, bid, revision, doc)
    return get(db, project, bid)
