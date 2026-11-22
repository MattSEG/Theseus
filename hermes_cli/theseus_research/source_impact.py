"""Find saved claims affected by a deliberate source update, within one project."""

import logging
import sqlite3
from functools import lru_cache

from . import briefs, library, refresh

log = logging.getLogger(__name__)
PAGE = 8
BACKFILL = 3
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS brief_citation_index(
        brief INTEGER PRIMARY KEY REFERENCES briefs(id),revision INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS brief_citation_cells(
        edition INTEGER NOT NULL,brief INTEGER NOT NULL REFERENCES briefs(id),
        position INTEGER NOT NULL,column_name TEXT NOT NULL,
        PRIMARY KEY(brief,position,column_name))""",
    "CREATE INDEX IF NOT EXISTS cited_edition_cells ON brief_citation_cells(edition,brief,position,column_name)",
    "CREATE TABLE IF NOT EXISTS source_impact_modes(project TEXT PRIMARY KEY,mode TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS source_impact_events(
        id INTEGER PRIMARY KEY,project TEXT NOT NULL,source INTEGER NOT NULL,edition INTEGER NOT NULL,
        UNIQUE(project,source))""",
)


def initialize(db):
    for statement in SCHEMA:
        db.execute(statement)


def index(db, brief, revision, rows):
    initialize(db)
    db.execute("DELETE FROM brief_citation_cells WHERE brief=?", (brief,))
    db.executemany(
        "INSERT INTO brief_citation_cells VALUES(?,?,?,?)",
        (
            (cell["edition"], brief, pos, name)
            for pos, row in enumerate(rows)
            for name, cell in row["cells"].items()
            if cell.get("edition")
        ),
    )
    db.execute(
        "INSERT OR REPLACE INTO brief_citation_index VALUES(?,?)", (brief, revision)
    )


def mode(db, project):
    row = db.execute(
        "SELECT mode FROM source_impact_modes WHERE project=?", (project,)
    ).fetchone()
    return row[0] if row else "automatic"


def backfill(db, project):
    older = db.execute(
        """SELECT b.id,b.revision FROM briefs b LEFT JOIN brief_citation_index i ON i.brief=b.id
        WHERE b.project=? AND (i.revision IS NULL OR i.revision<>b.revision)
        ORDER BY b.id DESC LIMIT ?""",
        (project, BACKFILL + 1),
    ).fetchall()
    for brief, revision in older[:BACKFILL]:
        doc = briefs.get(db, project, brief, revision)
        index(db, brief, revision, doc["rows"])
    return len(older) > BACKFILL


def query(db, project, source, edition, cursor=None, *, offer=False):
    current = library.accessible_edition(db, project, source=source)
    if current["edition"] != edition:
        raise ValueError("The selected source changed again. Open its latest edition.")
    incomplete = backfill(db, project)
    after = cursor if cursor is not None else [0, -1, ""]
    if (
        not isinstance(after, list)
        or len(after) != 3
        or type(after[0]) is not int
        or type(after[1]) is not int
        or not isinstance(after[2], str)
    ):
        raise ValueError("Use the cursor returned by the previous source comparison.")
    rows = db.execute(
        """SELECT b.id,b.revision,b.title,c.position,c.column_name FROM editions e
        JOIN brief_citation_cells c ON c.edition=e.id JOIN briefs b ON b.id=c.brief
        JOIN brief_citation_index i ON i.brief=b.id AND i.revision=b.revision
        WHERE e.source=? AND e.id<>? AND b.project=? AND (b.id,c.position,c.column_name)>(?,?,?)
        ORDER BY b.id,c.position,c.column_name LIMIT ?""",
        (source, edition, project, *after, PAGE + 1),
    ).fetchall()
    result = dict(
        source=source,
        edition=edition,
        title=current["title"],
        digest=current["digest"],
        pending_index=incomplete,
        offered=offer,
        checked=0,
        items=[],
        next_after=None,
    )
    if offer:
        return result if rows or incomplete else None
    for statement in refresh.REVIEW_SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)
    compare = refresh.compare_for(db, project)

    @lru_cache(maxsize=1)
    def get_doc(brief, revision):
        return briefs.get(db, project, brief, revision)

    for ref in rows[:PAGE]:
        doc = get_doc(ref["id"], ref["revision"])
        row = doc["rows"][ref["position"]]
        column = ref["column_name"]
        change = compare(ref["id"], row["name"], column, row["cells"][column])
        result["checked"] += 1
        if (
            not change
            or change["state"] in ("unchanged", "passage retained")
            or (change["decision"] and change["decision"]["choice"] == "keep")
        ):
            continue
        result["items"].append(
            dict(
                brief=ref["id"], revision=ref["revision"], title=ref["title"], **change
            )
        )
    if len(rows) > PAGE:
        last = rows[PAGE - 1]
        result["next_after"] = [last["id"], last["position"], last["column_name"]]
    return result


def for_chat(result):
    """Bound the tool receipt while preserving the source, scope and actual field comparisons."""
    return dict(
        source=result["source"],
        edition=result["edition"],
        sha256=result["digest"],
        checked=result["checked"],
        offered=result["offered"],
        incomplete=bool(
            result["pending_index"] or result["next_after"] or len(result["items"]) > 3
        ),
        changes=[
            dict(
                brief=c["brief"],
                title=c["title"],
                revision=c["revision"],
                row=c["row"],
                column=c["column"],
                state=c["state"],
                old_edition=c["old_edition"],
                prior=c["prior"][:240],
                current=c["value"][:240] if c["value"] else None,
                context_changes=[
                    {k: v[:180] for k, v in f.items()} for f in c["context_changes"][:3]
                ],
            )
            for c in result["items"][:3]
        ],
    )


def has_more(result):
    return bool(
        result
        and (
            result["items"]
            or result["offered"]
            or result["next_after"]
            or result["pending_index"]
        )
    )


def observe(db, project, source, edition):
    """An optional check never invalidates the import or changes a saved conclusion."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='briefs'").fetchone():
        return None
    db.execute("SAVEPOINT source_impact")
    try:
        initialize(db)
        choice = mode(db, project)
        if choice == "off":
            return None
        found = query(db, project, source, edition, offer=choice == "suggest")
        db.execute(
            "DELETE FROM source_impact_events WHERE project=? AND source=?",
            (project, source),
        )
        if has_more(found):
            db.execute(
                "INSERT INTO source_impact_events(project,source,edition) VALUES(?,?,?)",
                (project, source, edition),
            )
            return found
    except (ValueError, OSError, sqlite3.Error) as exc:
        db.execute("ROLLBACK TO source_impact")
        log.debug("Source dependency check deferred (%s)", type(exc).__name__)
    finally:
        db.execute("RELEASE source_impact")
    return None


def desktop(args):
    project = library.field(args, "project", 100)
    action = args["action"]
    with library.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        initialize(db)
        choice = mode(db, project)
        if action == "impact_mode":
            if "mode" in args:
                value = args["mode"]
                if value not in ("automatic", "suggest", "off") or choice != args.get(
                    "expected"
                ):
                    raise ValueError(
                        "Refresh the project before changing this setting."
                    )
                db.execute(
                    "INSERT OR REPLACE INTO source_impact_modes VALUES(?,?)",
                    (project, value),
                )
                if value == "off":
                    db.execute(
                        "DELETE FROM source_impact_events WHERE project=?", (project,)
                    )
                choice = value
            return dict(mode=choice)
        if action == "impact_recent":
            recent = db.execute(
                """SELECT v.source,v.edition,s.title FROM source_impact_events v
                JOIN sources s ON s.id=v.source LEFT JOIN research_pins p ON p.source=v.source AND p.project=v.project
                WHERE v.project=? AND v.edition=CASE WHEN s.project=v.project THEN s.current_id ELSE p.edition END
                ORDER BY v.id DESC LIMIT 5""",
                (project,),
            ).fetchall()
            return dict(items=[dict(r) for r in recent] if choice != "off" else [])
        source, edition = args.get("source"), args.get("edition")
        if type(source) is not int or type(edition) is not int:
            raise ValueError("Select a recorded source and edition.")
        if action == "impact_dismiss":
            db.execute(
                "DELETE FROM source_impact_events WHERE project=? AND source=? AND edition=?",
                (project, source, edition),
            )
            return dict(dismissed=True)
        if action == "impact_check":
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='briefs'"
            ).fetchone():
                raise ValueError("There are no saved briefs in this project.")
            result = query(db, project, source, edition, args.get("after"))
            if args.get("after") is None and not has_more(result):
                db.execute(
                    "DELETE FROM source_impact_events WHERE project=? AND source=? AND edition=?",
                    (project, source, edition),
                )
            return result
        raise ValueError("Unknown source review action.")
