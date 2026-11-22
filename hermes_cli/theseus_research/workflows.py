"""User-saved procedures and explicitly advanced, project-scoped checklists."""

import json
from . import briefs
from .library import field

DDL = """
CREATE TABLE IF NOT EXISTS research_workflows(id INTEGER PRIMARY KEY,origin TEXT NOT NULL,title TEXT NOT NULL,
 source_brief INTEGER NOT NULL,source_revision INTEGER NOT NULL,definition TEXT NOT NULL,
 UNIQUE(origin,title,source_brief,source_revision,definition));
CREATE TABLE IF NOT EXISTS workflow_jobs(id INTEGER PRIMARY KEY,project TEXT NOT NULL,workflow INTEGER NOT NULL,
 brief INTEGER NOT NULL UNIQUE,steps TEXT NOT NULL,progress TEXT NOT NULL);
"""


def steps(value):
    if not isinstance(value, str) or len(value) > 5000:
        raise ValueError("Supply up to 12 steps, one per line.")
    result = [s.strip() for s in value.splitlines() if s.strip()]
    if not 1 <= len(result) <= 12:
        raise ValueError("Supply one to twelve steps.")
    return result


def operate(db, project, args):
    db.executescript(briefs.DDL + DDL)
    action = args["action"]
    if action != "workflow_list":
        db.execute("BEGIN IMMEDIATE")
    if action == "workflow_save":
        source = briefs.get(db, project, args.get("brief"))
        if not any(c["value"] for r in source["rows"] for c in r["cells"].values()):
            raise ValueError("Choose a brief with a recorded result.")
        definition = dict(
            template=source["template"],
            columns=source["columns"],
            steps=steps(args.get("steps")),
            output=field(args, "output", 400),
            review=field(args, "review", 400),
        )
        db.execute(
            "INSERT OR IGNORE INTO research_workflows(origin,title,source_brief,source_revision,definition) VALUES(?,?,?,?,?)",
            (
                project,
                field(args, "title", 160),
                source["brief"],
                source["revision"],
                json.dumps(definition),
            ),
        )
    elif action == "workflow_start":
        wf = db.execute(
            "SELECT * FROM research_workflows WHERE id=?", (args.get("workflow"),)
        ).fetchone()
        if not wf:
            raise ValueError("Choose a saved workflow.")
        definition = json.loads(wf["definition"])
        title = field(args, "title", 180)
        question = field(args, "question", 1400)
        procedure = steps(args["steps"]) if "steps" in args else definition["steps"]
        existing = db.execute(
            "SELECT id FROM briefs WHERE project=? AND title=?", (project, title)
        ).fetchone()
        if existing:
            raise ValueError(
                "A brief already uses this title. Reopen it or choose another title."
            )
        bid = db.execute(
            "INSERT INTO briefs(project,title,template,revision) VALUES(?,?,?,0)",
            (project, title, definition["template"]),
        ).lastrowid
        briefs.store(
            db, bid, 1, dict(question=question, columns=definition["columns"], rows=[])
        )
        db.execute(
            "INSERT INTO workflow_jobs(project,workflow,brief,steps,progress) VALUES(?,?,?,?,?)",
            (project, wf["id"], bid, json.dumps(procedure), "[]"),
        )
    elif action == "workflow_advance":
        job = db.execute(
            "SELECT * FROM workflow_jobs WHERE id=? AND project=?",
            (args.get("job"), project),
        ).fetchone()
        if not job:
            raise ValueError("No workflow job in this project.")
        completed = json.loads(job["progress"])
        procedure = json.loads(job["steps"])
        if args.get("expected_step") != len(completed) or len(completed) > len(
            procedure
        ):
            raise ValueError("Refresh this job before recording a step.")
        brief = briefs.get(db, project, job["brief"])
        if len(completed) == len(procedure) and not any(
            c["value"] for r in brief["rows"] for c in r["cells"].values()
        ):
            raise ValueError("The output brief is empty. Fill it before signing off.")
        completed.append(
            dict(note=field(args, "note", 800), brief_revision=brief["revision"])
        )
        db.execute(
            "UPDATE workflow_jobs SET progress=? WHERE id=?",
            (json.dumps(completed), job["id"]),
        )
    elif action != "workflow_list":
        raise ValueError("Unknown workflow operation.")
    library = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM research_workflows ORDER BY id DESC LIMIT 100"
        )
    ]
    for item in library:
        item.update(json.loads(item.pop("definition")))
    jobs = [
        dict(r)
        for r in db.execute(
            """SELECT j.id,j.brief,b.title,j.workflow,j.steps,j.progress,w.definition
        FROM workflow_jobs j JOIN briefs b ON b.id=j.brief JOIN research_workflows w ON w.id=j.workflow WHERE j.project=? ORDER BY j.id DESC""",
            (project,),
        )
    ]
    for job in jobs:
        job["steps"] = json.loads(job["steps"])
        job["progress"] = json.loads(job["progress"])
        job["review"] = json.loads(job.pop("definition"))["review"]
    origins = [
        dict(r)
        for r in db.execute(
            "SELECT id,title FROM briefs WHERE project=? ORDER BY id DESC", (project,)
        )
    ]
    return dict(workflows=library, jobs=jobs, briefs=origins)
