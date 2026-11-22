"""Explicit commitments, waiting items and decisions across the owner's projects."""

from .library import field

STATES = ("Needs decision", "Ready", "Waiting", "Done")
DDL = """CREATE TABLE IF NOT EXISTS attention_items(id INTEGER PRIMARY KEY,project TEXT NOT NULL,title TEXT NOT NULL,
 state TEXT NOT NULL,minutes INTEGER,context TEXT NOT NULL,source INTEGER,edition INTEGER,revision INTEGER NOT NULL DEFAULT 1,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(project,title))"""


def operate(db, project, args):
    db.execute(DDL)
    action = args["action"]
    state = args.get("state")
    if action in ("attention_capture", "attention_change"):
        if state not in STATES:
            raise ValueError("Choose Needs decision, Ready, Waiting or Done.")
        db.execute("BEGIN IMMEDIATE")
    if action == "attention_capture":
        minutes = args.get("minutes")
        if minutes is not None and (type(minutes) != int or not 1 <= minutes <= 10080):
            raise ValueError("Use 1 to 10,080 minutes or leave the estimate blank.")
        source = args.get("source")
        edition = None
        if source is not None:
            row = db.execute(
                "SELECT current_id FROM sources WHERE id=? AND project=?",
                (source, project),
            ).fetchone()
            if not row:
                raise ValueError("Select a source from this project.")
            edition = row[0]
        title = field(args, "title", 240)
        context = field(args, "context", 1000, False)
        match = db.execute(
            "SELECT * FROM attention_items WHERE project=? AND title=?",
            (project, title),
        ).fetchone()
        if match and tuple(
            match[k] for k in ("state", "minutes", "context", "source", "edition")
        ) != (state, minutes, context, source, edition):
            raise ValueError(
                "A different item already uses this title in this project."
            )
        db.execute(
            "INSERT OR IGNORE INTO attention_items(project,title,state,minutes,context,source,edition) VALUES(?,?,?,?,?,?,?)",
            (project, title, state, minutes, context, source, edition),
        )
    elif action == "attention_change":
        cur = db.execute(
            "UPDATE attention_items SET state=?,revision=revision+1 WHERE project=? AND id=? AND revision=?",
            (state, project, args.get("item"), args.get("revision")),
        )
        if not cur.rowcount:
            raise ValueError("Refresh this item before changing it.")
    elif action != "attention_list":
        raise ValueError("Unknown attention operation.")
    where = []
    params = []
    if args.get("all_projects") is not True:
        where.append("project=?")
        params.append(project)
    if args.get("filter") in STATES:
        where.append("state=?")
        params.append(args["filter"])
    else:
        where.append("state<>'Done'")
    limit = args.get("max_minutes")
    if limit is not None:
        if type(limit) != int or limit < 1:
            raise ValueError("Use a positive whole-minute budget.")
        where.append("minutes<=?")
        params.append(limit)
    return {
        "items": [
            dict(r)
            for r in db.execute(
                "SELECT * FROM attention_items WHERE "
                + " AND ".join(where)
                + " ORDER BY CASE state WHEN 'Needs decision' THEN 1 WHEN 'Ready' THEN 2 WHEN 'Waiting' THEN 3 ELSE 4 END,id DESC LIMIT 250",
                params,
            )
        ]
    }
