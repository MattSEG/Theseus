"""Exact UTF-8 imports and revocable, project-scoped access to a user-selected file."""

import hashlib
import os
from pathlib import Path
import stat

from . import library

SUFFIXES = {".csv", ".tsv", ".md", ".json", ".txt"}
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS project_file_links(id INTEGER PRIMARY KEY AUTOINCREMENT,
        project TEXT NOT NULL, source INTEGER NOT NULL UNIQUE REFERENCES sources(id),
        directory TEXT NOT NULL, directory_device INTEGER NOT NULL,directory_inode INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS project_file_receipts(edition INTEGER PRIMARY KEY REFERENCES editions(id),
        digest TEXT NOT NULL,bytes INTEGER NOT NULL)""",
)


def initialize(db):
    for statement in SCHEMA:
        db.execute(statement)


def path_for(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("Choose a supported local text file.")
    path = Path(value)
    if (
        not path.is_absolute()
        or path.suffix.lower() not in SUFFIXES
        or ".." in path.parts
    ):
        raise ValueError("Choose an absolute CSV, TSV, JSON, Markdown or text file.")
    return path


def read_file(path, grant=None):
    parent = path.parent.resolve(strict=True)
    if grant and str(parent) != grant["directory"]:
        raise ValueError("The linked folder moved. Select the file again in Library.")
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        identity = os.fstat(directory)
        if grant and (identity.st_dev, identity.st_ino) != (
            grant["directory_device"],
            grant["directory_inode"],
        ):
            raise ValueError(
                "The linked folder was replaced. Select the file again in Library."
            )
        fd = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size > library.MAX_TEXT
                or before.st_nlink != 1
            ):
                raise ValueError(
                    "Choose a regular file up to 1 MB with a single filesystem link."
                )
            data = stream.read(library.MAX_TEXT + 1)
            after = os.fstat(stream.fileno())
            entry = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if (
                len(data) != before.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or (entry.st_dev, entry.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise ValueError("The file changed during the read. Retry the import.")
    finally:
        os.close(directory)
    try:
        body = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Choose a UTF-8 text file.") from None
    return body, (str(parent), identity.st_dev, identity.st_ino)


def record(db, result, body):
    size = len(body.encode("utf-8"))
    db.execute(
        "INSERT OR IGNORE INTO project_file_receipts VALUES(?,?,?)",
        (result["edition"], result["digest"], size),
    )
    result["verified_file"] = dict(sha256=result["digest"], bytes=size)


def selected(db, project, args):
    path = path_for(args.get("origin"))
    body = args.get("text")
    if not isinstance(body, str) or len(body) > library.MAX_TEXT:
        raise ValueError("Choose a UTF-8 text file up to 1 MB.")
    data = body.encode("utf-8")
    if len(data) > library.MAX_TEXT or hashlib.sha256(data).hexdigest() != args.get(
        "file_sha256"
    ):
        raise ValueError(
            "The received text differs from the selected file. Select it again."
        )
    location = None
    if args.get("allow_reimport") is True:
        disk, location = read_file(path)
        if body != disk:
            raise ValueError(
                "The selected file changed before it could be linked. Select it again."
            )
    db.execute("BEGIN IMMEDIATE")
    initialize(db)
    result = library.capture(
        db,
        project,
        dict(kind="file", title=path.name, origin=str(path), text=body),
        check_impact=True,
    )
    record(db, result, body)
    if location:
        previous = db.execute(
            "SELECT directory,directory_device,directory_inode FROM project_file_links WHERE project=? AND source=?",
            (project, result["source"]),
        ).fetchone()
        if previous is None or tuple(previous) != location:
            db.execute(
                "INSERT OR REPLACE INTO project_file_links(project,source,directory,directory_device,directory_inode) VALUES(?,?,?,?,?)",
                (project, result["source"], *location),
            )
    result["file_linked"] = bool(link(db, project, source=result["source"]))
    return result


def link(db, project, *, source=None, origin=None):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='project_file_links'"
    ).fetchone():
        return None
    return db.execute(
        """SELECT g.*,s.origin,s.title,s.current_id FROM project_file_links g JOIN sources s
        ON s.id=g.source AND s.project=g.project WHERE g.project=? AND
        ((? IS NOT NULL AND g.source=?) OR (? IS NOT NULL AND s.origin=?))""",
        (project, source, source, origin, origin),
    ).fetchone()


def refresh(db, project, args):
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    grant = link(db, project, source=args.get("source"))
    if not grant:
        raise ValueError(
            "This project has no file link for that source. In Library, select Allow chat to reimport, then choose the file."
        )
    if (
        type(args.get("expected_edition")) is not int
        or args["expected_edition"] != grant["current_id"]
    ):
        raise ValueError(
            "The selected edition changed. Check the current project source before reimporting."
        )
    body, _ = read_file(path_for(grant["origin"]), grant)
    result = library.capture(
        db,
        project,
        dict(kind="file", title=grant["title"], origin=grant["origin"], text=body),
        check_impact=True,
    )
    record(db, result, body)
    result["file_linked"] = True
    return result


def decorate(db, project, sources):
    files = [s for s in sources if s.get("kind") == "file"]
    if not files:
        return
    ids = {s["edition"] for s in files}
    marks = ",".join("?" for _ in ids)
    receipts = {}
    linked = set()
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='project_file_receipts'"
    ).fetchone():
        receipts = {
            r["edition"]: r
            for r in db.execute(
                "SELECT edition,digest,bytes FROM project_file_receipts WHERE edition IN ("
                + marks
                + ")",
                tuple(ids),
            )
        }
        sids = {s["source"] for s in files}
        linked = {
            r[0]
            for r in db.execute(
                "SELECT source FROM project_file_links WHERE project=? AND source IN ("
                + ",".join("?" for _ in sids)
                + ")",
                (project, *sids),
            )
        }
    for s in files:
        proof = receipts.get(s["edition"])
        if proof and proof["digest"] == s["digest"]:
            s["file_proof"] = dict(
                status="verified at import",
                sha256=proof["digest"],
                bytes=proof["bytes"],
                scope="Original file bytes matched this edition at import. Current disk contents were not checked by this lookup.",
            )
        else:
            s["file_proof"] = dict(
                status="archive only",
                scope="The archive has a text digest. No matching original-file byte receipt was recorded for this edition.",
            )
        if s["source"] in linked:
            s["file_linked"] = True


def desktop(args):
    project = library.field(args, "project", 100)
    with library.connection() as db:
        action = args["action"]
        if action == "capture_selected_file":
            return selected(db, project, args)
        db.execute("BEGIN IMMEDIATE")
        initialize(db)
        if action == "file_revoke":
            if not db.execute(
                "DELETE FROM project_file_links WHERE project=? AND source=? AND id=?",
                (project, args.get("source"), args.get("link")),
            ).rowcount:
                raise ValueError("This file link changed. Refresh the project.")
        rows = db.execute(
            """SELECT g.id AS link,g.source,s.current_id AS edition,s.title,s.origin FROM project_file_links g
            JOIN sources s ON s.id=g.source AND s.project=g.project WHERE g.project=? ORDER BY g.id DESC""",
            (project,),
        )
        return dict(files=[dict(r) for r in rows])
