"""Named collections reference archived editions. Each project keeps its own interpretation."""

from .library import accessible_edition, field, search

DDL = """CREATE TABLE IF NOT EXISTS library_collections(project TEXT NOT NULL,name TEXT NOT NULL,source INTEGER NOT NULL,
 edition INTEGER NOT NULL,added TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(project,name,source,edition));
CREATE TABLE IF NOT EXISTS collection_notes(project TEXT NOT NULL,source INTEGER NOT NULL,note TEXT NOT NULL,revision INTEGER NOT NULL,
 PRIMARY KEY(project,source));"""


def operate(db, project, args):
    db.executescript(DDL)
    action = args["action"]
    if action in ("collection_file", "collection_retitle"):
        db.execute("BEGIN IMMEDIATE")
        s = accessible_edition(db, project, args.get("source"), args.get("edition"))
        sid = s["source"]
        if action == "collection_file":
            name = field(args, "collection", 120)
            note = field(args, "note", 1800, False)
            old = db.execute(
                "SELECT note,revision FROM collection_notes WHERE project=? AND source=?",
                (project, sid),
            ).fetchone()
            if (
                old
                and old["note"] != note
                and args.get("expected_revision") != old["revision"]
            ):
                raise ValueError(
                    "The local annotation changed. Reopen it before replacing it."
                )
            if not old or old["note"] != note:
                db.execute(
                    """INSERT INTO collection_notes(project,source,note,revision) VALUES(?,?,?,1)
                    ON CONFLICT(project,source) DO UPDATE SET note=excluded.note,revision=collection_notes.revision+1""",
                    (project, sid, note),
                )
            db.execute(
                "INSERT OR IGNORE INTO library_collections(project,name,source,edition) VALUES(?,?,?,?)",
                (project, name, sid, s["edition"]),
            )
        else:
            if (
                s["origin_project"] != project
                or args.get("expected_title") != s["title"]
            ):
                raise ValueError(
                    "Reopen the original project and title before correcting shared metadata."
                )
            title = field(args, "title", 240)
            db.execute(
                "INSERT INTO source_catalog_titles(source,title) VALUES(?,?) ON CONFLICT(source) DO UPDATE SET title=excluded.title",
                (sid, title),
            )
            db.execute("UPDATE sources SET title=? WHERE id=?", (title, sid))
            db.execute("UPDATE source_search SET title=? WHERE rowid=?", (title, sid))
    elif action != "collection_list":
        raise ValueError("Unknown collection operation.")
    cross = args.get("all_projects") is True
    rows = [
        dict(r)
        for r in db.execute(
            """SELECT a.project,a.name AS collection,a.source,a.edition,s.title,s.project AS origin_project,
      n.note,n.revision,a.added,e.digest FROM library_collections a JOIN sources s ON a.source=s.id JOIN editions e ON a.edition=e.id
      LEFT JOIN collection_notes n ON n.project=a.project AND n.source=a.source WHERE (? OR a.project=?)
      AND (?='' OR a.name=?) ORDER BY a.name COLLATE NOCASE,a.project,a.source LIMIT 300""",
            (cross, project, args.get("filter", ""), args.get("filter", "")),
        )
    ]
    opts = search(db, project, {})["sources"]
    for src in opts:
        n = db.execute(
            "SELECT note,revision FROM collection_notes WHERE project=? AND source=?",
            (project, src["source"]),
        ).fetchone()
        src.update(note=n["note"] if n else "", revision=n["revision"] if n else 0)
    return dict(items=rows, sources=opts)
