"""Project-local layout preferences based on deliberate use of different artifacts."""

import hashlib
import json

DDL = """CREATE TABLE IF NOT EXISTS layout_uses(project TEXT NOT NULL,brief INTEGER NOT NULL,template TEXT NOT NULL,
 fingerprint TEXT NOT NULL,columns_json TEXT NOT NULL,revision INTEGER NOT NULL,used TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(project,brief,fingerprint));
CREATE TABLE IF NOT EXISTS layout_defaults(project TEXT NOT NULL,template TEXT NOT NULL,fingerprint TEXT NOT NULL,
 columns_json TEXT NOT NULL,PRIMARY KEY(project,template));"""


def setup(db):
    for statement in DDL.split(";"):
        if statement.strip():
            db.execute(statement)


def record_export(db, doc):
    if not any(c["value"] for row in doc["rows"] for c in row["cells"].values()):
        return
    setup(db)
    columns = json.dumps(doc["columns"], ensure_ascii=False)
    key = hashlib.sha256(columns.encode()).hexdigest()
    db.execute(
        """INSERT INTO layout_uses(project,brief,template,fingerprint,columns_json,revision) VALUES(?,?,?,?,?,?)
      ON CONFLICT(project,brief,fingerprint) DO UPDATE SET revision=excluded.revision,used=CURRENT_TIMESTAMP""",
        (doc["project"], doc["brief"], doc["template"], key, columns, doc["revision"]),
    )


def default_columns(db, project, template):
    setup(db)
    row = db.execute(
        "SELECT columns_json FROM layout_defaults WHERE project=? AND template=?",
        (project, template),
    ).fetchone()
    return json.loads(row[0]) if row else None


def operate(db, project, args):
    from .briefs import DDL as BRIEFS_DDL

    db.executescript(BRIEFS_DDL)
    setup(db)
    rows = db.execute(
        """SELECT u.*,b.title FROM layout_uses u JOIN briefs b ON b.id=u.brief
      WHERE u.project=? ORDER BY u.used DESC,u.brief DESC""",
        (project,),
    )
    groups = {}
    for row in rows:
        key = (row["template"], row["fingerprint"])
        g = groups.setdefault(
            key,
            dict(
                template=key[0],
                fingerprint=key[1],
                columns=json.loads(row["columns_json"]),
                receipts=[],
            ),
        )
        g["receipts"].append(
            dict(
                brief=row["brief"],
                title=row["title"],
                revision=row["revision"],
                exported=row["used"],
            )
        )
    action = args["action"]
    key = (args.get("template"), args.get("fingerprint"))
    if action == "preference_accept":
        g = groups.get(key)
        if not g or len(g["receipts"]) < 2:
            raise ValueError(
                "A suggested layout requires two distinct populated exports."
            )
        db.execute(
            """INSERT INTO layout_defaults(project,template,fingerprint,columns_json) VALUES(?,?,?,?)
          ON CONFLICT(project,template) DO UPDATE SET fingerprint=excluded.fingerprint,columns_json=excluded.columns_json""",
            (project, *key, json.dumps(g["columns"])),
        )
    elif action == "preference_undo":
        db.execute(
            "DELETE FROM layout_defaults WHERE project=? AND template=? AND fingerprint=?",
            (project, *key),
        )
    elif action != "preference_list":
        raise ValueError("Unknown preference action.")
    defaults = {
        (r[0], r[1])
        for r in db.execute(
            "SELECT template,fingerprint FROM layout_defaults WHERE project=?",
            (project,),
        )
    }
    return {
        "suggestions": [
            dict(g, active=k in defaults)
            for k, g in groups.items()
            if len(g["receipts"]) >= 2
        ]
    }
