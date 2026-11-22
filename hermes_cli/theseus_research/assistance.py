"""Local, bounded assistance for ordinary research work. Project changes remain explicit."""

import json
import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from hermes_constants import get_hermes_home

from . import briefs, library, search_exports, search_focus, search_trials

log = logging.getLogger(__name__)
MODES = ("automatic", "suggest", "off")
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS research_assist_modes(
        project TEXT PRIMARY KEY, mode TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS research_assist_exports(
        id INTEGER PRIMARY KEY, project TEXT NOT NULL, brief INTEGER NOT NULL, revision INTEGER NOT NULL,
        session TEXT NOT NULL, user_row INTEGER NOT NULL, state TEXT NOT NULL, created INTEGER NOT NULL,
        next_try INTEGER NOT NULL DEFAULT 0, check_id INTEGER, kind TEXT, reason TEXT, signal TEXT,
        UNIQUE(brief,revision,session,user_row))""",
    "CREATE INDEX IF NOT EXISTS assist_by_session ON research_assist_exports(session,state,id)",
    "CREATE INDEX IF NOT EXISTS assist_by_project ON research_assist_exports(project,state,id)",
    """CREATE TABLE IF NOT EXISTS research_assist_dismissals(
        project TEXT NOT NULL,signal TEXT NOT NULL,PRIMARY KEY(project,signal))""",
    """CREATE TABLE IF NOT EXISTS research_assist_receipts(
        job INTEGER PRIMARY KEY, trigger TEXT NOT NULL, completed INTEGER NOT NULL)""",
    search_trials.EXPORT_CHECK_DDL,
    search_trials.CHOICE_DDL,
)


def initialize(db):
    for statement in SCHEMA:
        db.execute(statement)


def mode(db, project):
    row = db.execute(
        "SELECT mode FROM research_assist_modes WHERE project=?", (project,)
    ).fetchone()
    return row[0] if row else "automatic"


def latest_turn(session):
    state = Path(get_hermes_home()) / "state.db"
    if not state.is_file():
        return None
    with closing(
        sqlite3.connect(state.as_uri() + "?mode=ro", uri=True, timeout=0.25)
    ) as trace:
        row = trace.execute(
            "SELECT id FROM messages WHERE session_id=? AND active=1 AND role='user' ORDER BY id DESC LIMIT 1",
            (session,),
        ).fetchone()
    return row[0] if row else None


def enqueue(db, doc, session):
    if (
        not isinstance(session, str)
        or not session
        or not any(c.get("edition") for r in doc["rows"] for c in r["cells"].values())
    ):
        return
    # An optional local diagnostic cannot change the outcome of the file operation.
    db.execute("SAVEPOINT assist_enqueue")
    try:
        initialize(db)
        selected = mode(db, doc["project"])
        if selected == "off":
            return
        user = latest_turn(session)
        if user:
            db.execute(
                """INSERT OR IGNORE INTO research_assist_exports(project,brief,revision,session,user_row,state,created)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    doc["project"],
                    doc["brief"],
                    doc["revision"],
                    session,
                    user,
                    "pending" if selected == "automatic" else "offered",
                    int(time.time()),
                ),
            )
    except (sqlite3.Error, OSError) as exc:
        db.execute("ROLLBACK TO assist_enqueue")
        log.debug("Export assistance unavailable (%s)", type(exc).__name__)
    finally:
        db.execute("RELEASE assist_enqueue")


def signal_for(row, result, finding):
    material = sorted(
        {
            (c["edition"], c["quote_digest"])
            for c in result["cells"]
            if any(
                (c["row"], c["column"]) == (v["row"], v["column"])
                for v in finding["cells"]
            )
        }
    )
    return search_trials.digest(
        json.dumps([row["project"], result["version"], finding["kind"], material])
    )


def reconcile(db, row, trigger):
    args = dict(brief=row["brief"], revision=row["revision"])
    try:
        checked = search_exports.check(
            db, row["project"], args, session=row["session"], user_row=row["user_row"]
        )
    except search_exports.AwaitingExportReceipt as exc:
        if time.time() - row["created"] < 300:
            db.execute(
                "UPDATE research_assist_exports SET next_try=? WHERE id=?",
                (int(time.time()) + 1, row["id"]),
            )
            return
        reason = str(exc)
    except ValueError as exc:
        reason = str(exc)
    else:
        finding = search_exports.next_check(checked["result"]["cells"])
        db.execute(
            "UPDATE research_assist_exports SET state='checked',check_id=?,kind=?,reason=?,signal=? WHERE id=?",
            (
                checked["id"],
                finding["kind"],
                finding["reason"],
                signal_for(row, checked["result"], finding),
                row["id"],
            ),
        )
        from .search_followup import observe

        observe(db, row, checked["result"])
        reason = None
    if reason is not None:
        db.execute(
            "UPDATE research_assist_exports SET state='skipped',reason=? WHERE id=?",
            (reason, row["id"]),
        )
    db.execute(
        "INSERT OR IGNORE INTO research_assist_receipts VALUES(?,?,?)",
        (row["id"], trigger, int(time.time())),
    )


def drain(db, field, value, trigger):
    assert field in ("session", "project")
    for row in db.execute(
        f"SELECT * FROM research_assist_exports WHERE {field}=? AND state='pending' AND next_try<=? ORDER BY id LIMIT 2",
        (value, int(time.time())),
    ).fetchall():
        reconcile(db, row, trigger)


def queued(home, session):
    path = home / "research" / "library.sqlite"
    if not path.is_file():
        return False
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)
    ) as db:
        return bool(
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='research_assist_exports'"
            ).fetchone()
            and db.execute(
                "SELECT 1 FROM research_assist_exports WHERE session=? AND state='pending' AND next_try<=? LIMIT 1",
                (session, int(time.time())),
            ).fetchone()
        )


def after_turn(session_id="", **_):
    try:
        if session_id and queued(Path(get_hermes_home()), session_id):
            with library.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                initialize(db)
                drain(db, "session", session_id, "turn completed")
    except (OSError, sqlite3.Error) as exc:
        log.debug("Deferred export assistance (%s)", type(exc).__name__)


def present(db, row):
    state, kind, project = row["state"], row["kind"], row["project"]
    signal = row["signal"] or ("offer:" + str(row["id"]))
    dismissed = bool(
        db.execute(
            "SELECT 1 FROM research_assist_dismissals WHERE project=? AND signal=?",
            (project, signal),
        ).fetchone()
    )
    active = mode(db, project) != "off" and not dismissed
    if state == "checked":
        result = json.loads(row["result"])
        choice = search_trials.project_choice(db, project)
        active = (
            active
            and kind in ("gain", "regression", "shared gap", "edition changed")
            and result["version"] == search_focus.VERSION
        )
        if (kind == "gain" and choice["focused"]) or (
            kind == "regression" and not choice["focused"]
        ):
            active = False
        if active and result["collection"] != search_trials.fingerprint(
            search_trials.collection(db, project)
        ):
            active = False
    else:
        active = active and state == "offered"
    return dict(
        id=row["id"],
        project=project,
        brief=row["brief"],
        revision=row["revision"],
        title=row["title"],
        state=state,
        kind=kind,
        reason=row["reason"],
        check=row["check_id"],
        trigger=row["trigger"],
        actionable=bool(active),
        dismissed=dismissed,
        created=row["created"],
    )


SELECT = """SELECT a.*,b.title,c.result,t.trigger FROM research_assist_exports a JOIN briefs b ON b.id=a.brief AND b.project=a.project
LEFT JOIN search_export_checks c ON c.id=a.check_id
LEFT JOIN research_assist_receipts t ON t.job=a.id"""


def desktop(args):
    action = args["action"]
    project = library.field(args, "project", 100, required=action != "assist_chat")
    if action == "assist_chat":
        session = library.field(args, "session", 300)
        # Unrelated chats never initialize a database or schedule a task.
        path = Path(get_hermes_home()) / "research" / "library.sqlite"
        if not path.is_file():
            return {"items": []}
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as probe:
            if (
                not probe.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='research_assist_exports'"
                ).fetchone()
                or not probe.execute(
                    "SELECT 1 FROM research_assist_exports WHERE session=? LIMIT 1",
                    (session,),
                ).fetchone()
            ):
                return {"items": []}
    with library.connection() as db:
        initialize(db)
        db.execute("BEGIN IMMEDIATE")
        if action == "assist_chat":
            drain(db, "session", session, "chat displayed")
            rows = db.execute(
                SELECT
                + " WHERE a.session=? AND a.user_row=? ORDER BY a.id DESC LIMIT 8",
                (session, latest_turn(session)),
            ).fetchall()
            items = [present(db, r) for r in rows]
            return {
                "items": [i for i in items if i["actionable"]][:1],
                "pending": any(i["state"] == "pending" for i in items),
            }
        if action == "assist_set_mode":
            value = args.get("mode")
            if value not in MODES or args.get("expected") != mode(db, project):
                raise ValueError(
                    "Refresh the project before changing its assistance mode."
                )
            db.execute(
                "INSERT INTO research_assist_modes VALUES(?,?) ON CONFLICT(project) DO UPDATE SET mode=excluded.mode",
                (project, value),
            )
            if value != "automatic":
                db.execute(
                    "UPDATE research_assist_exports SET state=? WHERE project=? AND state IN ('pending','offered')",
                    ("offered" if value == "suggest" else "cancelled", project),
                )
            return {"mode": value}
        if action == "assist_followups":
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='briefs'"
            ).fetchone():
                return {"followups": []}
            from .search_followup import history

            return {"followups": history(db, project)}
        if action == "assist_project":
            drain(db, "project", project, "project opened")
            rows = (
                db.execute(
                    SELECT + " WHERE a.project=? ORDER BY a.id DESC LIMIT 10",
                    (project,),
                ).fetchall()
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='briefs'"
                ).fetchone()
                else []
            )
            return dict(mode=mode(db, project), items=[present(db, r) for r in rows])
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='briefs'").fetchone():
            raise ValueError(
                "The selected assistance item does not belong to this project."
            )
        row = db.execute(
            SELECT + " WHERE a.project=? AND a.id=?", (project, args.get("id"))
        ).fetchone()
        if not row:
            raise ValueError(
                "The selected assistance item does not belong to this project."
            )
        if action == "assist_dismiss":
            db.execute(
                "INSERT OR IGNORE INTO research_assist_dismissals VALUES(?,?)",
                (project, row["signal"] or "offer:" + str(row["id"])),
            )
            return {"dismissed": True}
        if action == "assist_check":
            if row["state"] == "offered" and mode(db, project) != "off":
                db.execute(
                    "UPDATE research_assist_exports SET state='pending' WHERE id=?",
                    (row["id"],),
                )
                reconcile(db, row, "user requested")
            return {"id": row["id"]}
        if action == "assist_apply":
            if (
                row["kind"] not in ("gain", "regression")
                or not present(db, row)["actionable"]
            ):
                raise ValueError(
                    "This recommendation is no longer current. Recheck the project evidence."
                )
            choice = search_trials.configure(
                db,
                project,
                dict(
                    arm="focused" if row["kind"] == "gain" else "current",
                    expected=args.get("expected"),
                ),
            )
            from .search_followup import enroll

            enroll(db, project, choice, row)
            return {"choice": choice}
        if action == "assist_evidence" and row["result"]:
            result = json.loads(row["result"])
            doc = briefs.get(db, project, row["brief"], row["revision"])
            sources = search_exports.citations(
                db,
                project,
                doc,
                {s["edition"] for s in search_trials.collection(db, project)},
            )
            quoted = {(c["row"], c["column"]): c for c in sources}
            cells = []
            for c in result["cells"]:
                source = quoted.get((c["row"], c["column"]))
                if (
                    not source
                    or search_trials.digest(source["quote"]) != c["quote_digest"]
                    or source["digest"] != c["digest"]
                ):
                    raise ValueError(
                        "The archived passage differs from the check receipt."
                    )
                cells.append(
                    dict(
                        row=c["row"],
                        column=c["column"],
                        title=c["title"],
                        edition=c["edition"],
                        quote=source["quote"],
                        search=c["search"],
                        read=c["original_read"],
                    )
                )
            return dict(
                choice=search_trials.project_choice(db, project),
                cells=cells,
                queries=result["queries"],
                file_digest=result["digest"],
                version=result["version"],
            )
        raise ValueError("There is no completed check for this item.")
