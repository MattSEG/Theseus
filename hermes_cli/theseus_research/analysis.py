"""Auditable descriptive statistics over a user-selected source edition."""

import json
from .measurement_stats import records, summarize
from .library import field, accessible_edition

DDL = """CREATE TABLE IF NOT EXISTS measurement_runs(id INTEGER PRIMARY KEY,project TEXT NOT NULL,
 title TEXT NOT NULL,source INTEGER NOT NULL,edition INTEGER NOT NULL, parameters TEXT NOT NULL,report TEXT NOT NULL,
 UNIQUE(project,title,edition,parameters))"""


def source_text(db, project, source, edition=None):
    return accessible_edition(db, project, source, edition)


def store_run(db,project,source,title,params,report):
    db.execute(DDL)
    fields=(project,title,source["id"],source["edition"],json.dumps(params))
    db.execute("INSERT OR IGNORE INTO measurement_runs(project,title,source,edition,parameters,report) VALUES(?,?,?,?,?,?)",
               (*fields,json.dumps(report)))
    return db.execute("SELECT id FROM measurement_runs WHERE project=? AND title=? AND source=? AND edition=? AND parameters=?",fields).fetchone()[0]


def operate(db, project, args):
    db.execute(DDL)
    action = args["action"]
    if action == "analysis_record":
        run = db.execute(
            "SELECT source,edition FROM measurement_runs WHERE project=? AND id=?",
            (project, args.get("run")),
        ).fetchone()
        if not run:
            raise ValueError("No saved analysis in this project.")
        source = source_text(db, project, *run)
        header, data = records(source["body"])
        n = args.get("record")
        if type(n) != int or not 2 <= n <= len(data) + 1:
            raise ValueError("Choose a data record number.")
        return dict(edition=run["edition"], record=n, header=header, values=data[n - 2])
    if action == "analysis_columns":
        source = source_text(db, project, args.get("source"))
        header, rows = records(source["body"])
        return dict(columns=header, records=len(rows), edition=source["edition"])
    if action == "analysis_run":
        source = source_text(db, project, args.get("source"), args.get("edition"))
        title = field(args, "title", 180)
        params = {
            k: field(args, k, 150, k != "group") for k in ("measure", "group", "unit")
        }
        report = summarize(source, params)
        store_run(db,project,source,title,params,report)
    elif action != "analysis_list":
        raise ValueError("Unknown measurement operation.")
    out = []
    for row in db.execute(
        "SELECT id,title,source,edition,parameters,report FROM measurement_runs WHERE project=? ORDER BY id DESC LIMIT 50",
        (project,),
    ):
        r = dict(row)
        r.update(
            parameters=json.loads(row["parameters"]), report=json.loads(row["report"])
        )
        out.append(r)
    return {"runs": out}
