"""Local, versioned sources for the research workbench."""
import hashlib
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from hermes_constants import get_hermes_home

MAX_TEXT = 1_000_000
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, origin TEXT NOT NULL,
 title TEXT NOT NULL, kind TEXT NOT NULL, current_id INTEGER,
 UNIQUE(project,origin));
CREATE TABLE IF NOT EXISTS editions(
 id INTEGER PRIMARY KEY, source INTEGER NOT NULL REFERENCES sources(id),
 digest TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(source,digest));
CREATE TABLE IF NOT EXISTS source_catalog_titles(source INTEGER PRIMARY KEY REFERENCES sources(id),title TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_pins(project TEXT NOT NULL,source INTEGER NOT NULL REFERENCES sources(id),
 edition INTEGER NOT NULL REFERENCES editions(id),annotation TEXT NOT NULL DEFAULT '',added TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(project,source));
CREATE TABLE IF NOT EXISTS research_pin_changes(
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, source INTEGER NOT NULL REFERENCES sources(id),
 previous INTEGER NOT NULL REFERENCES editions(id), chosen INTEGER NOT NULL REFERENCES editions(id),
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS project_pin_history ON research_pin_changes(project,source,previous);
CREATE VIRTUAL TABLE IF NOT EXISTS source_search USING fts5(title, body, tokenize='porter unicode61');
CREATE INDEX IF NOT EXISTS project_sources ON sources(project,id);
"""

@contextmanager
def connection():
    root = Path(get_hermes_home()) / "research"
    root.mkdir(mode=0o700, exist_ok=True)
    db = sqlite3.connect(root / "library.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(SCHEMA)
        with db:
            yield db
    finally:
        db.close()


def field(args, key, maximum, required=True):
    value = args.get(key, "")
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"Supply {key} as text up to {maximum} characters.")
    return value.strip()


def capture(db, project, args, *, check_impact=False):
    title = field(args, "title", 240)
    field(args, "text", MAX_TEXT, required=False)
    body = args.get("text", "")
    origin = field(args, "origin", 4096, required=False)
    kind = args.get("kind", "note")
    if kind not in ("note", "file", "web"):
        raise ValueError("Choose a note, file or web source.")
    if kind == "web":
        url = urlsplit(origin)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            raise ValueError("Supply a public page URL without credentials.")
    elif not body.strip():
        raise ValueError("The source contains no text.")
    elif kind == "file" and not Path(origin).is_absolute():
        raise ValueError("A file needs its original absolute path.")
    if kind == "note" and not origin:
        origin = "note:" + hashlib.sha256((title + "\n" + body).encode()).hexdigest()
    preferred=db.execute("SELECT c.title FROM sources s JOIN source_catalog_titles c ON c.source=s.id WHERE s.project=? AND s.origin=?",(project,origin)).fetchone()
    if preferred:title=preferred[0]
    digest = hashlib.sha256(body.encode()).hexdigest()
    db.execute("INSERT INTO sources(project,origin,title,kind) VALUES(?,?,?,?) ON CONFLICT(project,origin) DO UPDATE SET title=excluded.title",
               (project, origin, title, kind))
    source, previous = db.execute("SELECT id,current_id FROM sources WHERE project=? AND origin=?", (project, origin)).fetchone()
    db.execute("INSERT OR IGNORE INTO editions(source,digest,body) VALUES(?,?,?)", (source, digest, body))
    edition = db.execute("SELECT id FROM editions WHERE source=? AND digest=?", (source, digest)).fetchone()[0]
    db.execute("UPDATE sources SET current_id=? WHERE id=?", (edition, source))
    db.execute("DELETE FROM source_search WHERE rowid=?", (source,))
    db.execute("INSERT INTO source_search(rowid,title,body) VALUES(?,?,?)", (source,title,body))
    result = {"source": source, "edition": edition, "digest": digest, "title": title}
    if check_impact and previous != edition:
        from .table_assist import offer
        offer(db,project,result,origin,body)
    if check_impact and previous and previous != edition:
        from .source_impact import observe
        impact = observe(db, project, source, edition)
        if impact:
            result["affected_briefs"] = impact
    return result


def search(db, project, args, *, focused=False):
    query = field(args, "query", 500, required=False)
    terms = list(dict.fromkeys(re.findall(r"[^\W_]+", query, re.UNICODE)))[:20]
    if not db.in_transaction:
        db.execute("BEGIN")
    count = db.execute("""SELECT (SELECT count(*) FROM sources WHERE project=?)
        + (SELECT count(*) FROM research_pins WHERE project=?)""", (project, project)).fetchone()[0]
    columns = """s.id AS source,e.id AS edition,s.title,s.kind,s.origin,e.created,e.digest,
        CASE WHEN length(CAST(e.body AS BLOB))<=1200 THEN e.body END AS short_text"""
    if terms:
        expression = " OR ".join('"' + term + '"' for term in terms)
        rows = db.execute(f"""SELECT {columns},snippet(source_search,1,'','','…',28) AS excerpt
            FROM source_search JOIN sources s ON s.id=source_search.rowid JOIN editions e ON e.id=s.current_id
            WHERE source_search MATCH ? AND s.project=? ORDER BY bm25(source_search,3,1),s.id LIMIT 50""", (expression,project))
    else:
        rows = db.execute(f"""SELECT {columns},substr(e.body,1,220) AS excerpt FROM sources s
            JOIN editions e ON e.id=s.current_id WHERE s.project=? ORDER BY s.id DESC LIMIT 50""",(project,))
    local=[dict(row) for row in rows]
    if focused and terms:
        from .search_focus import finish_local_quantities
        finish_local_quantities(db, local)
    pinned=db.execute("""SELECT s.id AS source,e.id AS edition,s.title,s.kind,s.origin,s.project AS shared_from,
      e.created,e.digest,e.body,s.current_id AS origin_current_edition,
      CASE WHEN s.current_id=p.edition THEN 'matches origin' ELSE 'origin uses a different edition' END AS pin_status
      FROM research_pins p JOIN sources s ON p.source=s.id JOIN editions e ON p.edition=e.id
      WHERE p.project=? ORDER BY p.added DESC""",(project,))
    folded_terms = [term.casefold() for term in terms]
    for row in pinned:
        item=dict(row);body=item.pop("body")
        folded_body = body.casefold() if terms else ""
        folded_title = item["title"].casefold() if terms else ""
        if not terms or any(t in folded_title or t in folded_body for t in folded_terms):
            item["excerpt"] = body[:220]
            item["short_text"] = body if len(body) <= 1200 else None
            if focused and terms and len(body) > 220:
                from .search_focus import excerpt_start
                if hashlib.sha256(body.encode()).hexdigest() != item["digest"]:
                    item.update(excerpt="", short_text=None, evidence_scope="edition receipt mismatch")
                else:
                    start = excerpt_start(body, terms, folded=folded_body)
                    passage = body[start:start+220]
                    if start and passage not in item["excerpt"]:
                        item["_matched"] = (start, passage)
            local.append(item)
        if len(local)>=100:break
    # A bounded number of short results can carry their entire verified evidence.
    # Longer indexed snippets remain useful for discovery but do not establish completeness.
    budget = 6000
    complete = 0
    for result in local:
        body = result.pop("short_text")
        result.setdefault("evidence_scope", "excerpt only")
        if body is None:
            continue
        raw = body.encode("utf-8")
        if len(raw) > min(1200, budget):
            continue
        if hashlib.sha256(raw).hexdigest() != result["digest"]:
            result.update(excerpt="", evidence_scope="edition receipt mismatch")
        elif not body.strip():
            result.update(excerpt="", evidence_scope="bookmark only")
        else:
            result.pop("excerpt_start", None)
            result.update(excerpt=body, evidence_scope="complete verified edition")
            budget -= len(raw)
            complete += 1
    result = {"sources":local, "coverage":{"project":project, "scope":"current local sources and selected shared editions",
            "available":count, "returned":len(local), "complete_verified":complete,
            "extent":("no selected sources" if count==0 else "exhaustive verified source text" if count==complete
                      else "partial source text")}}
    if focused:
        from .search_focus import attach_passages
        result["coverage"].update(attach_passages(local))
    return result


def accessible_edition(db,project,source=None,edition=None):
    if source is None and edition is None:
        raise ValueError("Select a source or an edition.")
    row=db.execute("""SELECT s.id,s.id AS source,s.project AS origin_project,s.title,s.origin,s.kind,s.current_id,
       e.id AS edition,e.created,e.digest,e.body,s.current_id=e.id AS current FROM sources s JOIN editions e ON e.source=s.id
       LEFT JOIN research_pins p ON p.source=s.id AND p.project=:p
       WHERE (s.project=:p OR p.edition=e.id OR
            (p.source IS NOT NULL AND EXISTS(SELECT 1 FROM research_pin_changes h
                WHERE h.project=:p AND h.source=s.id AND h.previous=e.id))) AND (:source IS NULL OR s.id=:source)
       AND e.id=COALESCE(:ed,CASE WHEN s.project=:p THEN s.current_id ELSE p.edition END) LIMIT 1""",
       {"p":project,"source":source,"ed":edition}).fetchone()
    if not row:raise ValueError("No matching source edition in this project.")
    return row


def source_editions(db, project, args):
    source = args.get("source")
    before = args.get("before")
    if type(source) is not int or source < 1:
        raise ValueError("Select a source.")
    if before is not None and (type(before) is not int or before < 1):
        raise ValueError("Use the edition cursor from the previous page.")
    rows = db.execute("""
        SELECT e.id AS edition, e.created, e.digest,
               CASE WHEN s.project != :project THEN CASE WHEN p.edition=e.id THEN 'Pinned' ELSE 'Previously pinned' END
                    WHEN e.id = s.current_id THEN 'Current' ELSE 'Archived' END AS status
          FROM editions e JOIN sources s ON s.id = e.source
          LEFT JOIN research_pins p ON p.source = s.id AND p.project = :project
         WHERE s.id = :source AND (s.project = :project OR p.edition = e.id OR (p.source IS NOT NULL AND EXISTS(
            SELECT 1 FROM research_pin_changes h WHERE h.project=:project AND h.source=s.id AND h.previous=e.id)))
           AND (:before IS NULL OR e.id < :before)
         ORDER BY e.id DESC LIMIT 21
    """, {"source": source, "project": project, "before": before}).fetchall()
    if not rows and before is None:
        raise ValueError("No matching source edition in this project.")
    page = [dict(row) for row in rows[:20]]
    return {"editions": page, "next_before": page[-1]["edition"] if len(rows) > 20 else None}


def read(db, project, args):
    start=args.get("start",0)
    if type(start)!=int or start<0:raise ValueError("Use a nonnegative text offset.")
    item=dict(accessible_edition(db,project,args.get("source"),args.get("edition")))
    body = item.pop("body")
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != item["digest"]:
        raise ValueError("The stored text does not match its edition receipt. Treat this source as unverified.")
    end=start+12000
    item.update(length=len(body),text=body[start:end],next_start=end if end<len(body) else None)
    from .local_files import decorate
    decorate(db,project,[item])
    return item


def project_names(db):
    tables = ("sources","research_pins","briefs","attention_items","workflow_jobs","measurement_runs",
              "scenarios","crawls","planning_decisions")
    available = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    union = " UNION ".join("SELECT project FROM "+name for name in tables if name in available)
    return [r[0] for r in db.execute("SELECT project FROM ("+union+") ORDER BY project COLLATE NOCASE,project")]


def name_key(name):
    text = unicodedata.normalize("NFKC", name).casefold()
    return tuple(re.findall(r"[^\W_]+", text))


READ_PROJECT = {"analysis_preview","project_next","search","read","source_editions","brief_list","brief_read","brief_passage",
                "refresh_review","refresh_compare","refresh_history",
                "decision_list","scenario_list","attention_list","analysis_list","workflow_list",
                "collection_list","paired_list","reporting_compare","evidence_history"}


def execute(args, *, resolve_tool_reads=False, session_id=None):
    requested = field(args, "project", 100)
    project = requested
    with connection() as db:
        action = args.get("action")
        if resolve_tool_reads and isinstance(action, str) and action in READ_PROJECT:
            names = project_names(db)
            if requested not in names:
                key = name_key(requested)
                matches = [name for name in names if key and name_key(name) == key]
                if len(matches)>1:
                    return {"status":"ambiguous_project","requested_project":requested,"candidates":matches,
                            "message":"Ask which of these projects the user means before reading its records."}
                if matches:
                    project = matches[0]
        if action == "search":
            from .search_trials import agent_search
            result = agent_search(db,project,args,session_id if resolve_tool_reads else None)
            from .local_files import decorate
            decorate(db,project,result.get("sources",[]))
        elif action == "refresh_file":
            from .local_files import refresh
            result=refresh(db,project,args)
        elif action=="capture" and resolve_tool_reads and args.get("kind")=="file":
            from .local_files import link,refresh
            db.execute("BEGIN IMMEDIATE")
            grant=link(db,project,origin=args.get("origin"))
            if grant:
                if args.get("expected_edition") is not None and args["expected_edition"]!=grant["current_id"]:
                    raise ValueError("The source changed. Check its current edition.")
                result=refresh(db,project,dict(source=grant["source"],expected_edition=grant["current_id"]))
            else:
                result=capture(db,project,args,check_impact=True)
                result["file_provenance"]="Submitted text. The original file bytes were not independently verified."
        elif action == "brief_export" and resolve_tool_reads and session_id:
            from .briefs import operate
            result = operate(db,project,args,export_session=session_id)
        else:
            result = dispatch(db,project,args,action)
        if resolve_tool_reads and "affected_briefs" in result:
            from .source_impact import for_chat
            result["affected_briefs"] = for_chat(result["affected_briefs"])
        if project != requested:
            result.update(requested_project=requested, resolved_project=project)
        return result


def dispatch(db, project, args, action):
    if action == "analysis_preview":
        from .table_assist import operate
        return operate(db,project,args)
    if action == "project_next":
        from .project_next import snapshot
        return snapshot(db,project,args.get("max_minutes"))
    if isinstance(action,str) and action.startswith("crawl_"):
        from .crawl import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("brief_"):
        from .briefs import operate
        return operate(db,project,args)
    if action in ("refresh_compare","refresh_apply","refresh_review","refresh_keep","refresh_reopen","refresh_history"):
        from .refresh import operate
        return operate(db,project,args)
    if action in ("scenario_save","scenario_list"):
        from .scenarios import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("analysis_"):
        from .analysis import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("workflow_"):
        from .workflows import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("attention_"):
        from .attention import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("discovery_"):
        from .discovery import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("preference_"):
        from .preferences import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("evidence_"):
        from .evidence import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("paired_"):
        from .paired import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("collection_"):
        from .collections import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("reporting_"):
        from .reporting import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("decision_"):
        from .decisions import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("audience_"):
        from .audiences import operate
        return operate(db,project,args)
    if isinstance(action,str) and action.startswith("handoff_"):
        from .handoff import operate
        return operate(db,project,args)
    if action == "capture":
        return capture(db, project, args, check_impact=True)
    if action == "source_editions":
        return source_editions(db, project, args)
    if action == "read":
        return read(db, project, args)
    if action == "projects":
        return {"projects":project_names(db)}
    raise ValueError("Unknown research library action.")
