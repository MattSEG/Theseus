"""Portable, internal project handoffs with an offline reading order and original evidence."""

import csv
import hashlib
import html
import io
import json
from pathlib import Path
from . import analysis, briefs, measurement_stats
from .bundles import publish
from .library import accessible_edition


def document(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2).encode()


def outstanding(db, project):
    tables = {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    result = []
    if "attention_items" in tables:
        result.extend(
            dict(kind="Action", **dict(r))
            for r in db.execute(
                "SELECT title,state,context FROM attention_items WHERE project=? AND state<>'Done' ORDER BY id",
                (project,),
            )
        )
    if "planning_decisions" in tables:
        for r in db.execute(
            """SELECT d.title,d.state,v.data FROM planning_decisions d JOIN planning_decision_versions v
            ON d.id=v.decision AND v.revision=d.revision WHERE project=? AND d.state<>'Reviewed' ORDER BY d.id""",
            (project,),
        ):
            result.append(
                dict(
                    kind="Decision",
                    title=r["title"],
                    state=r["state"],
                    context=json.loads(r["data"]),
                )
            )
    if "workflow_jobs" in tables:
        for r in db.execute(
            """SELECT b.title,j.steps,j.progress,w.definition FROM workflow_jobs j JOIN research_workflows w ON w.id=j.workflow
            JOIN briefs b ON b.id=j.brief WHERE j.project=? ORDER BY j.id""",
            (project,),
        ):
            steps = json.loads(r["steps"]) + [
                "Review: " + json.loads(r["definition"])["review"]
            ]
            count = len(json.loads(r["progress"]))
            if count < len(steps):
                result.append(
                    dict(
                        kind="Workflow",
                        title=r["title"],
                        state="Open",
                        context=steps[count],
                    )
                )
    return result


def operate(db, project, args):
    db.executescript(briefs.DDL)
    db.execute(analysis.DDL)
    if args["action"] == "handoff_list":
        return dict(
            briefs=[
                dict(r)
                for r in db.execute(
                    "SELECT id,title,revision FROM briefs WHERE project=? ORDER BY id DESC",
                    (project,),
                )
            ],
            runs=[
                dict(r)
                for r in db.execute(
                    "SELECT id,title,edition FROM measurement_runs WHERE project=? ORDER BY id DESC",
                    (project,),
                )
            ],
        )
    if args["action"] != "handoff_export":
        raise ValueError("Unknown handoff operation.")
    db.execute("BEGIN")
    files = {}
    cited = set()
    order = []
    source_index = []

    def add(path, body, label):
        files[path] = body
        order.append((path, label))

    if args.get("brief"):
        doc = briefs.get(db, project, args["brief"], args.get("revision"))
        add(
            "brief.csv",
            briefs.csv_text(doc).encode(),
            doc["title"] + " · revision " + str(doc["revision"]),
        )
        add(
            "brief.json",
            document(
                {
                    k: doc[k]
                    for k in ("title", "question", "revision", "columns", "rows")
                }
            ),
            "Editable brief data and cell citations",
        )
        cited.update(
            (c["source"], c["edition"])
            for r in doc["rows"]
            for c in r["cells"].values()
            if c.get("edition")
        )
    if args.get("run"):
        r = db.execute(
            "SELECT * FROM measurement_runs WHERE project=? AND id=?",
            (project, args["run"]),
        ).fetchone()
        if not r:
            raise ValueError("Choose a saved analysis in this project.")
        s = accessible_edition(db, project, r["source"], r["edition"])
        params = json.loads(r["parameters"])
        report = json.loads(r["report"])
        out = io.StringIO(newline="")
        w = csv.DictWriter(out, fieldnames=list(report["groups"][0]))
        w.writeheader()
        for group in report["groups"]:
            w.writerow({k: briefs.safe_cell(str(v)) for k, v in group.items()})
        add(
            "analysis/summary.csv",
            out.getvalue().encode(),
            r["title"] + " · summary in " + params["unit"],
        )
        cited.add((s["source"], s["edition"]))
        records_path = f"../sources/source-{s['source']}-edition-{s['edition']}.txt"
        script = f"""from pathlib import Path
import hashlib, json
from measurement_stats import summarize
data=Path({records_path!r}).read_bytes()
actual=hashlib.sha256(data).hexdigest()
assert actual=={s["digest"]!r}, "Source digest differs"
result=summarize({{"body":data.decode("utf-8"),"digest":actual}},json.loads(Path("parameters.json").read_text()))
assert result==json.loads(Path("expected.json").read_text()), "Result differs"
print(json.dumps(result["groups"],indent=2))
"""
        notebook = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {
                    "name": "python3",
                    "display_name": "Python 3",
                    "language": "python",
                }
            },
            "cells": [
                {
                    "id": "repeat-measurement",
                    "cell_type": "code",
                    "metadata": {},
                    "execution_count": None,
                    "outputs": [],
                    "source": script.splitlines(True),
                }
            ],
        }
        add(
            "analysis/reproduce.ipynb",
            document(notebook),
            "Reproduce the calculation in Jupyter with this folder as its working directory",
        )
        files.update(
            {
                "analysis/measurement_stats.py": Path(
                    measurement_stats.__file__
                ).read_bytes(),
                "analysis/parameters.json": document(params),
                "analysis/expected.json": document(report),
            }
        )
    if not files:
        raise ValueError("Select a brief or a measurement run.")
    if len(cited) > 40:
        raise ValueError("Select outputs with at most 40 cited editions per handoff.")
    for sid, ed in sorted(cited):
        s = accessible_edition(db, project, sid, ed)
        path = f"sources/source-{sid}-edition-{ed}.txt"
        files[path] = s["body"].encode()
        if hashlib.sha256(files[path]).hexdigest() != s["digest"]:
            raise ValueError("An archived source no longer matches its receipt.")
        source_index.append(
            dict(
                source=sid,
                edition=ed,
                title=s["title"],
                original_project=s["origin_project"],
                created=s["created"],
                file=path,
                origin=s["origin"]
                if s["kind"] == "web"
                else Path(s["origin"]).name
                if s["kind"] == "file"
                else "Saved note",
                digest=s["digest"],
            )
        )
    if source_index:
        add(
            "sources.json",
            document(source_index),
            "Source references, origin, dates and digests",
        )
    if args.get("include_planning") is True:
        add(
            "open-work.json",
            document(outstanding(db, project)),
            "Outstanding decisions, commitments and workflow steps",
        )
    esc = html.escape
    links = "".join(f'<li><a href="{p}">{esc(label)}</a></li>' for p, label in order)
    links += "".join(
        f'<li><a href="{v["file"]}">{esc(v["title"])} · edition {v["edition"]}</a></li>'
        for v in source_index
    )
    files["index.html"] = (
        f'<!doctype html><html lang="en"><meta charset="utf-8"><title>{esc(project)} · Internal handoff</title><style>body{{max-width:52rem;margin:4rem auto;padding:0 1rem;font:17px/1.6 system-ui;color:#182c32}}h1{{font:2.4rem Georgia,serif}}</style><h1>{esc(project)}</h1><p>Internal project handoff. Includes selected working material and its archived evidence.</p><h2>Reading order</h2><ol>{links}</ol><a href="manifest.json">Verify file digests</a></html>'.encode()
    )
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}
    manifest = document(dict(project=project, sha256=hashes))
    files["manifest.json"] = manifest
    if sum(map(len, files.values())) > 25_000_000:
        raise ValueError("Select a smaller set of outputs, up to 25 MB.")
    root = publish("handoff-" + hashlib.sha256(manifest).hexdigest()[:20], files)
    return dict(
        folder=str(root),
        index=str(root / "index.html"),
        files=len(files),
        sources=len(cited),
    )
