"""Bounded traversal of project-scoped relations; source records remain authoritative."""
import hashlib
import json
import unicodedata


def normalized(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


SCHEMA = """
CREATE TABLE IF NOT EXISTS frames(id INTEGER PRIMARY KEY, project TEXT, relation TEXT, digest TEXT);
CREATE INDEX IF NOT EXISTS frames_digest ON frames(digest);
CREATE TABLE IF NOT EXISTS frame_roles(frame INTEGER, position INTEGER, role TEXT, value TEXT,
    anchor TEXT, record INTEGER, excerpt TEXT, PRIMARY KEY(frame,position));
CREATE INDEX IF NOT EXISTS frame_anchor ON frame_roles(anchor,frame);
"""

CITATIONS = """SELECT r.role,r.value,r.anchor,r.record,r.excerpt,d.path,s.first_line,s.last_line,d.digest
               FROM frame_roles r JOIN spans s ON s.rowid=r.record JOIN docs d ON d.id=s.doc
               WHERE r.frame=? ORDER BY r.position"""


def add_frame(db, project, relation, roles):
    project = normalized(project)
    relation = relation.strip()
    if not 1 <= len(relation) <= 100 or not 2 <= len(roles) <= 6:
        raise ValueError("provide a short relation and two to six cited roles")
    prepared = []
    for role, value, rec, excerpt in roles:
        role, value = role.strip(), value.strip()
        if not (0 < len(role) <= 64 and 0 < len(value) <= 160 and 0 < len(excerpt) <= 320
                and rec.isascii() and rec.isdigit() and len(rec) <= 15):
            raise ValueError("each role needs a name, value, record number and bounded excerpt")
        row = db.execute("""SELECT s.body FROM spans s JOIN docs d ON d.id=s.doc
            JOIN names n ON n.name=d.name JOIN source_status ss ON ss.doc=d.id
            WHERE s.rowid=? AND ss.status='current' AND n.anchor=(SELECT anchor FROM names WHERE name=?)""",
            (int(rec), project)).fetchone()
        if not row or excerpt not in row[0] or value not in excerpt:
            raise ValueError("each value must occur in its exact excerpt from a current project record")
        prepared.append((role, value, normalized(value), int(rec), excerpt))
    if len({normalized(role[0]) for role in prepared}) != len(prepared):
        raise ValueError("use distinct role names within a frame")
    prepared.sort(key=lambda r: normalized(r[0]))
    digest = hashlib.sha256(json.dumps([relation, prepared], ensure_ascii=False).encode()).hexdigest()
    old = db.execute("""SELECT f.id FROM frames f JOIN names n ON n.name=f.project WHERE f.digest=?
                      AND n.anchor=(SELECT anchor FROM names WHERE name=?) ORDER BY f.id LIMIT 1""",
                     (digest, project)).fetchone()
    if old:
        return old[0]
    fid = db.execute("INSERT INTO frames(project,relation,digest) VALUES(?,?,?)", (project, relation, digest)).lastrowid
    db.executemany("INSERT INTO frame_roles VALUES(?,?,?,?,?,?,?)",
                   ((fid, i, *r) for i, r in enumerate(prepared)))
    return fid


def trace(db, project, start, hops, limit):
    frontier = [normalized(start)]
    seen = set()
    results = []
    for depth in range(1, hops + 1):
        upcoming = []
        for anchor in frontier:
            matches = db.execute("""SELECT DISTINCT f.id,f.relation FROM frame_roles m JOIN frames f ON f.id=m.frame
                JOIN names n ON n.name=f.project WHERE m.anchor=? AND n.anchor=(SELECT anchor FROM names WHERE name=?)
                AND NOT EXISTS(SELECT 1 FROM frame_roles r JOIN spans s ON s.rowid=r.record
                    JOIN source_status st ON st.doc=s.doc WHERE r.frame=f.id AND st.status!='current')
                ORDER BY f.id DESC LIMIT ?""", (anchor, normalized(project), limit))
            for fid, relation in matches:
                if fid in seen:
                    continue
                seen.add(fid)
                roles = []
                for role, value, term, record, excerpt, path, lo, hi, sha in db.execute(CITATIONS, (fid,)):
                    roles.append(dict(role=role, value=value, record=record, excerpt=excerpt,
                                      source=path, lines=[lo, hi], sha256=sha))
                    if term != anchor:
                        upcoming.append(term)
                results.append(dict(frame=fid, relation=relation, hop=depth, matched=anchor, roles=roles))
                if len(results) == limit:
                    return results
        frontier = list(dict.fromkeys(upcoming))
    return results
