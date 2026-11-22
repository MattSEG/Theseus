"""Versioned local text records with exact section citations."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata

import frames
from hermes_cli.artifact_inputs import open_artifact


def key(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def terms(value):
    return " OR ".join('"' + word + '"' for word in re.findall(r"\w+", key(value))[:32])


def evidence_window(body, query, first_line, last_line, budget=1800):
    if len(body) <= budget:
        return {"text": body, "lines": [first_line, last_line]}

    def folded(word):
        if word.isascii():
            return word.lower()
        return "".join(c for c in unicodedata.normalize("NFKD", word.casefold()) if not unicodedata.combining(c))

    words = {folded(word) for word in re.findall(r"[^\W_]+", query)[:32]}
    hits = []
    for match in re.finditer(r"[^\W_]+", body):
        word = folded(match[0])
        if word in words:
            hits.append((match.start(), word))
    focus = 0
    if hits:
        frequency = Counter(word for _, word in hits)
        counts = Counter()
        end = 0
        best = (-1, -1.0)
        for start, word in hits:
            while end < len(hits) and hits[end][0] < start + min(240, budget):
                counts[hits[end][1]] += 1
                end += 1
            # Distinct nearby terms beat isolated occurrences. Rare terms break ties.
            score = (len(counts), sum(1 / frequency[token] for token in counts))
            if score > best:
                best = score
                focus = start
            counts[word] -= 1
            if not counts[word]:
                del counts[word]
    left = min(max(0, focus - budget // 3), len(body) - budget)
    right = left + budget
    # Prefer complete lines when a nearby break still retains the chosen hit.
    if left:
        newline = body.find("\n", left, min(focus, left + 120))
        if newline >= 0:
            left = newline + 1
    if right < len(body):
        newline = body.rfind("\n", max(focus, right - 120), right)
        if newline >= 0:
            right = newline + 1
    if left and body[left - 1:left + 1] == "\r\n":
        left += 1
    if body[right - 1:right + 1] == "\r\n":
        right -= 1
    line_break = re.compile(r"\r\n|[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]")
    text = body[left:right]
    lo = first_line + sum(1 for _ in line_break.finditer(body, 0, left))
    hi = lo + max(0, len(text.splitlines()) - 1)
    return {"text": text, "lines": [lo, hi], "section_lines": [first_line, last_line],
            "section_characters": [left, right]}


def sections(text):
    heading, start, lines = "Document", 1, []
    for n, line in enumerate(text.splitlines(keepends=True), 1):
        if re.match(r"^#{1,6}\s", line):
            if lines:
                yield heading, start, n - 1, "".join(lines)
            heading, start, lines = line.strip("# \t\n\r"), n, []
        lines.append(line)
    if lines:
        yield heading, start, start + len(lines) - 1, "".join(lines)


PROCEDURE_STEPS = """SELECT ps.record,ps.excerpt,d.path,s.first_line,s.last_line,d.digest,
    state.status
    FROM procedure_steps ps JOIN spans s ON s.rowid=ps.record JOIN docs d ON d.id=s.doc JOIN source_status state ON state.doc=d.id
    WHERE ps.procedure=? ORDER BY ps.position"""


def compose(db, args, parser):
    title = args.title.strip()
    if not 1 <= len(title) <= 160 or not 1 <= len(args.step) <= 16:
        parser.error("choose a title up to 160 characters and one to sixteen cited steps")
    project = key(args.project)
    steps = []
    for record, excerpt in args.step:
        if not record.isascii() or not record.isdigit() or len(record) > 15 or not 1 <= len(excerpt.strip()) <= 800:
            parser.error("each step needs a source record number and an exact excerpt up to 800 characters")
        source = db.execute("""SELECT body FROM spans s JOIN docs d ON d.id=s.doc
            JOIN names n ON n.name=d.name JOIN source_status state ON state.doc=d.id AND state.status='current' WHERE s.rowid=?
            AND n.anchor=(SELECT anchor FROM names WHERE name=?)""", (int(record), project)).fetchone()
        if not source or excerpt not in source[0]:
            parser.error("step must quote a current source in the selected project")
        steps.append((int(record), excerpt))
    digest = hashlib.sha256(json.dumps([title, steps], ensure_ascii=False).encode()).hexdigest()
    existing = db.execute("""SELECT p.id FROM procedures p JOIN names n ON n.name=p.name
        WHERE p.digest=? AND n.anchor=(SELECT anchor FROM names WHERE name=?) ORDER BY p.id LIMIT 1""",
        (digest, project)).fetchone()
    if existing:
        return existing[0]
    pid = db.execute("INSERT INTO procedures(name,title,digest) VALUES(?,?,?)", (project, title, digest)).lastrowid
    db.executemany("INSERT INTO procedure_steps VALUES(?,?,?,?)",
                   ((pid, pos, record, excerpt) for pos, (record, excerpt) in enumerate(steps)))
    return pid


def recall_procedures(db, args):
    rows = db.execute("""SELECT p.id,p.title FROM procedures p JOIN names n ON n.name=p.name
        WHERE n.anchor=(SELECT anchor FROM names WHERE name=?) AND instr(lower(p.title),lower(?))>0
        ORDER BY p.id DESC""", (key(args.project), args.query.strip()))
    count = 0
    for pid, title in rows:
        steps = [dict(record=record, text=excerpt, source=path, lines=[lo, hi], sha256=digest,
                      status=status)
                 for record, excerpt, path, lo, hi, digest, status in db.execute(PROCEDURE_STEPS, (pid,))]
        current = all(step["status"] == "current" for step in steps)
        if current or args.include_stale:
            yield dict(procedure=pid, title=title, status="current" if current else "stale", steps=steps)
            count += 1
            if count == 4:
                return


def source_bytes(path):
    with open_artifact(path) as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read(2_000_001)
        after = path.stat()
        if len(raw) > 2_000_000:
            raise ValueError("select a UTF-8 Markdown or text document no larger than 2 MB")
        if (not os.path.samestat(before, after) or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError("source changed during reading")
    return raw


def check_source(database, record):
    if not database.exists():
        raise ValueError("unknown citation record")
    with sqlite3.connect(database.absolute().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("""SELECT d.id,d.path,d.digest FROM spans s JOIN docs d ON d.id=s.doc
                            WHERE s.rowid=?""", (record,)).fetchone()
    if row is None:
        raise ValueError("unknown citation record")
    snapshot, path, remembered = row
    observed = None
    try:
        observed = hashlib.sha256(source_bytes(Path(path))).hexdigest()
        state = "matching" if observed == remembered else "changed"
    except FileNotFoundError:
        state = "missing"
    except (ValueError, OSError):
        state = "unverifiable"
    return dict(record=record, snapshot=snapshot, source=path, remembered_sha256=remembered,
                disk_sha256=observed, disk_state=state)


def main():
    parser = argparse.ArgumentParser()
    cmd = parser.add_subparsers(dest="cmd", required=True)
    add = cmd.add_parser("remember")
    add.add_argument("project")
    add.add_argument("file", type=Path)
    search = cmd.add_parser("recall")
    search.add_argument("project")
    search.add_argument("query")
    merge = cmd.add_parser("merge-name")
    merge.add_argument("project")
    merge.add_argument("alias")
    dream = cmd.add_parser("dream", help="preview exact punctuation variants of project names")
    dream.add_argument("--apply", action="store_true")
    show = cmd.add_parser("quote")
    show.add_argument("id", type=int)
    cmd.add_parser("check-source", help="compare a cited record with its original file on disk").add_argument("id", type=int)
    timeline = cmd.add_parser("timeline", help="show observed changes to one source")
    timeline.add_argument("project")
    timeline.add_argument("file", type=Path)
    make = cmd.add_parser("compose", help="save ordered excerpts from current project records")
    make.add_argument("project")
    make.add_argument("title")
    make.add_argument("--step", nargs=2, metavar=("RECORD", "EXCERPT"), action="append", required=True)
    procedures = cmd.add_parser("procedures", help="find up to four procedures by title")
    procedures.add_argument("project")
    procedures.add_argument("query")
    procedures.add_argument("--include-stale", action="store_true")
    cmd.add_parser("conflicts", help="show source paths with conflicting active versions").add_argument("project")
    frame = cmd.add_parser("frame", help="index a relation with cited roles")
    frame.add_argument("project")
    frame.add_argument("relation")
    frame.add_argument("--role", nargs=4, action="append", required=True, metavar=("ROLE", "VALUE", "RECORD", "EXCERPT"))
    trace = cmd.add_parser("trace", help="follow current cited relationships from an exact anchor")
    trace.add_argument("project")
    trace.add_argument("anchor")
    trace.add_argument("--hops", type=int, choices=[1, 2], default=1)
    trace.add_argument("--coverage", action="store_true", help="report whether the frame limit omitted eligible matches within the requested hops")
    trace.add_argument("--limit", type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(os.environ["HERMES_HOME"]) / "project-recall"
    if args.cmd == "check-source":
        try:
            receipt = check_source(root / "records.sqlite", args.id)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(receipt, ensure_ascii=False))
        if receipt["disk_state"] != "matching":
            raise SystemExit(2)
        return
    root.mkdir(exist_ok=True, mode=0o700)
    db = sqlite3.connect(root / "records.sqlite")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS names(name TEXT PRIMARY KEY, anchor TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, name TEXT, path TEXT,
            digest TEXT, data BLOB, UNIQUE(name,path,digest));
        CREATE TABLE IF NOT EXISTS current(name TEXT,path TEXT,doc INTEGER,PRIMARY KEY(name,path));
        CREATE VIEW IF NOT EXISTS source_groups AS
            SELECT n.anchor,c.path,count(DISTINCT d.digest) AS versions,min(d.digest) AS digest,min(d.id) AS representative
            FROM current c JOIN names n ON n.name=c.name JOIN docs d ON d.id=c.doc GROUP BY n.anchor,c.path;
        CREATE VIEW IF NOT EXISTS source_status AS
            SELECT d.id AS doc,CASE WHEN g.versions=1 AND g.digest=d.digest THEN 'current'
                WHEN g.versions>1 AND EXISTS(SELECT 1 FROM current c WHERE c.doc=d.id) THEN 'conflict'
                ELSE 'superseded' END AS status
            FROM docs d JOIN names n ON n.name=d.name
            LEFT JOIN source_groups g ON g.anchor=n.anchor AND g.path=d.path;
        CREATE TABLE IF NOT EXISTS activations(
            id INTEGER PRIMARY KEY, doc INTEGER NOT NULL, observed TEXT, origin TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS activations_doc ON activations(doc);
        CREATE TABLE IF NOT EXISTS procedures(id INTEGER PRIMARY KEY,name TEXT,title TEXT,digest TEXT,
            UNIQUE(name,digest));
        CREATE TABLE IF NOT EXISTS procedure_steps(procedure INTEGER,position INTEGER,record INTEGER,excerpt TEXT,
            PRIMARY KEY(procedure,position));
        CREATE VIRTUAL TABLE IF NOT EXISTS spans USING fts5(heading,body,doc UNINDEXED,
            first_line UNINDEXED,last_line UNINDEXED);
    """)
    db.executescript(frames.SCHEMA)
    with db:
        writing = args.cmd in {"remember", "frame", "compose", "merge-name"} or (
            args.cmd == "dream" and args.apply)
        # Old stores can have a current pointer without a known activation date.
        missing_history = """FROM current WHERE NOT EXISTS
            (SELECT 1 FROM activations WHERE doc=current.doc)"""
        migrate = db.execute("SELECT 1 " + missing_history + " LIMIT 1").fetchone() is not None
        if writing or migrate:
            db.execute("BEGIN IMMEDIATE")
            if migrate:
                db.execute("""INSERT INTO activations(doc, observed, origin)
                    SELECT current.doc, NULL, 'existing-current' """ + missing_history)
        else:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
        if args.cmd == "remember":
            path = args.file.resolve(strict=True)
            if path.suffix.lower() not in {".md", ".txt"}:
                parser.error("select one UTF-8 Markdown or text document under 2 MB")
            try:
                raw = source_bytes(path)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            body = raw.decode("utf-8-sig")
            name = key(args.project)
            db.execute("INSERT OR IGNORE INTO names VALUES (?,?)", (name, name))
            values = (name, str(path), hashlib.sha256(raw).hexdigest())
            row = db.execute("SELECT id FROM docs WHERE name=? AND path=? AND digest=?", values).fetchone()
            if row:
                doc = row[0]
            else:
                doc = db.execute("INSERT INTO docs(name,path,digest,data) VALUES(?,?,?,?)",
                                 (*values, raw)).lastrowid
                db.executemany("INSERT INTO spans VALUES(?,?,?,?,?)", (
                    (heading, text, doc, lo, hi) for heading, lo, hi, text in sections(body)))
            previous = db.execute("SELECT doc FROM current WHERE name=? AND path=?", (name, str(path))).fetchone()
            aliases = db.execute("""DELETE FROM current WHERE path=? AND name!=? AND name IN
                (SELECT name FROM names WHERE anchor=(SELECT anchor FROM names WHERE name=?))""",
                (str(path), name, name)).rowcount
            if previous != (doc,) or aliases:
                db.execute("INSERT INTO activations(doc,observed,origin) VALUES(?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),'remember')", (doc,))
            db.execute("INSERT OR REPLACE INTO current VALUES(?,?,?)", (name, str(path), doc))
            print(json.dumps({"snapshot": doc, "sha256": values[-1]}))
        elif args.cmd == "frame":
            try:
                print(json.dumps({"frame": frames.add_frame(db, args.project, args.relation, args.role)}))
            except ValueError as exc:
                parser.error(str(exc))
        elif args.cmd == "trace":
            fetch_limit = args.limit + 1 if args.coverage else args.limit
            matches = frames.trace(db, args.project, args.anchor, args.hops, fetch_limit)
            if args.coverage:
                result = {"hops": args.hops, "limit": args.limit, "more_matches": len(matches) > args.limit,
                          "frames": matches[:args.limit]}
            else:
                result = matches
            print(json.dumps(result, ensure_ascii=False))
        elif args.cmd == "conflicts":
            rows = db.execute("""SELECT g.path,n.name,d.id,d.digest FROM source_groups g
                JOIN names n ON n.anchor=g.anchor JOIN current c ON c.name=n.name AND c.path=g.path
                JOIN docs d ON d.id=c.doc WHERE g.versions>1 AND g.anchor=(SELECT anchor FROM names WHERE name=?)
                ORDER BY g.path,n.name""", (key(args.project),))
            for path, alias, doc, digest in rows:
                print(json.dumps(dict(source=path, alias=alias, snapshot=doc, sha256=digest, status="conflict")))
        elif args.cmd == "compose":
            print(json.dumps({"procedure": compose(db, args, parser)}))
        elif args.cmd == "procedures":
            for result in recall_procedures(db, args):
                print(json.dumps(result, ensure_ascii=False))
        elif args.cmd == "timeline":
            rows = db.execute("""SELECT a.id, d.id, d.digest, a.observed, a.origin FROM activations a
                JOIN docs d ON d.id=a.doc JOIN names n ON n.name=d.name
                WHERE n.anchor=(SELECT anchor FROM names WHERE name=?) AND d.path=? ORDER BY a.id""",
                (key(args.project), str(args.file.resolve())))
            for event, snapshot, digest, observed, origin in rows:
                print(json.dumps(dict(event=event, snapshot=snapshot, sha256=digest, observed=observed, origin=origin)))
        elif args.cmd == "dream":
            groups = {}
            for (anchor,) in db.execute("SELECT DISTINCT anchor FROM names ORDER BY anchor"):
                # Ignore punctuation and spacing only. Never merge on approximate spelling.
                normalized = "".join(c for c in anchor if c.isalnum())
                if normalized:
                    groups.setdefault(normalized, []).append(anchor)
            for group in groups.values():
                if len(group) < 2:
                    continue
                primary = max(group, key=lambda word: (word.count(" "), -len(word)))
                print(json.dumps({"names": group, "anchor": primary, "applied": args.apply}))
                if args.apply:
                    for alias in group:
                        db.execute("UPDATE names SET anchor=? WHERE anchor=?", (primary, alias))
        elif args.cmd == "merge-name":
            name, alias = key(args.project), key(args.alias)
            row = db.execute("SELECT anchor FROM names WHERE name=?", (name,)).fetchone()
            if not row:
                parser.error("unknown primary project")
            old = db.execute("SELECT anchor FROM names WHERE name=?", (alias,)).fetchone()
            if old:
                db.execute("UPDATE names SET anchor=? WHERE anchor=?", (row[0], old[0]))
            else:
                db.execute("INSERT INTO names VALUES(?,?)", (alias,row[0]))
            print(json.dumps({"alias": alias, "anchor": row[0]}))
        else:
            sql = """SELECT spans.rowid,docs.path,docs.digest,first_line,last_line,body,docs.id,
                     state.status FROM spans
                     JOIN docs ON docs.id=spans.doc JOIN source_status state ON state.doc=docs.id """
            if args.cmd == "quote":
                rows = db.execute(sql + "WHERE spans.rowid=?", (args.id,))
            else:
                query = terms(args.query)
                if not query:
                    parser.error("enter search words")
                rows = db.execute(sql + """JOIN source_groups g ON g.representative=docs.id AND g.versions=1
                    JOIN names ON names.name=docs.name WHERE spans MATCH ? AND names.anchor=
                    (SELECT anchor FROM names WHERE name=?) ORDER BY bm25(spans,8,1), spans.rowid LIMIT 4""",
                    (query, key(args.project)))
            for record, path, digest, lo, hi, body, snapshot, current in rows:
                excerpt = ({"lines": [lo, hi], "text": body} if args.cmd == "quote"
                           else evidence_window(body, args.query, lo, hi))
                print(json.dumps(dict(record=record, source=path, sha256=digest,
                                      snapshot=snapshot, status=current, **excerpt), ensure_ascii=False))


if __name__ == "__main__":
    main()
