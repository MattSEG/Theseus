"""Replay the searches behind a verified CSV, using its cited passages as a minimum check."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from hermes_constants import get_hermes_home

from . import briefs, evidence, library, search_focus, search_trials

MAX_TURN_ROWS = 100
MAX_SEARCHES = 8
MAX_CITATIONS = 24
MAX_RECEIPTS = 12
TOOLS = {"research_library", "research_brief"}


def parsed(text):
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
    if text.startswith("<untrusted_tool_result") and text.endswith(
        "</untrusted_tool_result>"
    ):
        text = text.partition("\n\n")[2].rsplit("\n</untrusted_tool_result>", 1)[0]
    try:
        return json.loads(text)
    except ValueError:
        return None


def operations(encoded):
    for call in parsed(encoded) or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function", {})
        if not isinstance(fn, dict):
            continue
        args = parsed(fn.get("arguments"))
        if not isinstance(args, dict):
            continue
        entries = (
            args.get("calls", [])
            if fn.get("name") == "tool_call"
            else [dict(name=fn.get("name"), arguments=args)]
        )
        if isinstance(entries, str):
            entries = parsed(entries)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("name") in TOOLS:
                yield (call.get("id"), entry["name"]), parsed(entry.get("arguments"))


def authoring_turn(db, session, end_id, project, doc, path):
    beginning = db.execute(
        "SELECT max(id) FROM messages WHERE session_id=? AND role='user' AND active=1 AND id<?",
        (session, end_id),
    ).fetchone()[0]
    if beginning is None:
        return None
    rows = db.execute(
        """SELECT id,role,tool_name,tool_call_id,
        CASE WHEN length(tool_calls)<=180000 THEN tool_calls END AS calls,
        CASE WHEN role='tool' AND length(content)<=300000 THEN content END AS receipt
        FROM messages WHERE session_id=? AND active=1 AND id>? AND id<=?
        AND (role='assistant' AND tool_calls IS NOT NULL OR role='tool' AND tool_name IN ('research_library','research_brief'))
        ORDER BY id LIMIT ?""",
        (session, beginning, end_id, MAX_TURN_ROWS + 1),
    ).fetchall()
    if len(rows) > MAX_TURN_ROWS:
        raise ValueError("The recorded turn exceeds the replay limit.")
    pending = {}
    authored = None
    exported = False
    searches, reads = [], []
    project_ops = {}
    for row in rows:
        if row["role"] == "assistant":
            if not isinstance(parsed(row["calls"]), list):
                raise ValueError("The authoring turn contains incomplete tool calls.")
            for key, args in operations(row["calls"]):
                if key in pending or not key[0] or not isinstance(args, dict):
                    raise ValueError("The saved turn has ambiguous tool-call receipts.")
                pending[key] = (args, (len(searches), len(reads),row["id"]))
            continue
        name = row["tool_name"]
        matched = pending.pop((row["tool_call_id"], name), None)
        if matched is None:
            raise ValueError("A tool receipt could not be linked to its request.")
        args, seen_at_request = matched
        reply = parsed(row["receipt"])
        if args.get("project") != project and not (
            isinstance(reply, dict)
            and reply.get("requested_project") == args.get("project")
            and reply.get("resolved_project") == project
        ):
            continue
        if not isinstance(reply, dict):
            raise ValueError(
                "A project tool reply is incomplete or exceeds the replay limit."
            )
        action = args.get("action")
        project_ops[row["id"]]=(name,action)
        if name == "research_brief":
            if (
                action in ("brief_create", "brief_edit", "refresh_apply")
                and reply.get("brief") == doc["brief"]
                and reply.get("project") == project
                and reply.get("revision") == doc["revision"]
                and all(reply.get(k) == doc[k] for k in ("question", "columns", "rows"))
            ):
                authored = seen_at_request
            if (
                row["id"] == end_id
                and action == "brief_export"
                and args.get("brief") == doc["brief"]
                and reply.get("revision") == doc["revision"]
                and reply.get("path") == path
            ):
                exported = True
        elif (
            action == "search"
            and isinstance(reply.get("sources"), list)
            and reply.get("coverage", {}).get("project") == project
        ):
            searches.append(
                (library.field(args, "query", 500, False), reply["sources"],reply.get("field_check"))
            )
        elif action == "read" and isinstance(reply.get("text"), str):
            reads.append(reply)
    if not (authored and exported):
        return None
    search_count, read_count, author_request = authored
    other_tools = []
    if any(stamp is not None for _,_,stamp in searches[:search_count]):
        trace = db.execute("""SELECT id,tool_name FROM messages WHERE session_id=? AND active=1
            AND role='tool' AND id>? AND id<? ORDER BY id LIMIT 65""",(session,beginning,author_request)).fetchall()
        for t in trace:
            if t["tool_name"] in ("tool_search","tool_describe"):
                continue
            if project_ops.get(t["id"]) in (("research_library","search"),("research_library","read")):
                continue
            other_tools.append(t["tool_name"] or "unknown tool")
        if len(trace)>64:other_tools.append("trace over limit")

    if not 1 <= search_count <= MAX_SEARCHES:
        raise ValueError(
            f"An eligible authoring turn needs 1–{MAX_SEARCHES} completed project searches."
        )
    return dict(
        session=session,
        turn=beginning,
        searches=searches[:search_count],
        reads=reads[:read_count],
        other_tools=sorted(set(other_tools)),
        later_searches=len(searches) - search_count,
    )


class AwaitingExportReceipt(ValueError):
    """The export returned, but the session has not persisted its tool receipt yet."""


def original(project, doc, path, *, session=None, user_row=None):
    state = Path(get_hermes_home()) / "state.db"
    if not state.is_file():
        raise ValueError("No saved chat history is available for this export.")
    with closing(sqlite3.connect(state.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='messages_fts'"
        ).fetchone():
            raise ValueError(
                "The indexed chat receipts for this profile are unavailable."
            )
        criteria = ""
        params = ['"' + Path(path).name + '"']
        if session is not None:
            criteria = """ AND m.session_id=? AND m.id>? AND NOT EXISTS (
                SELECT 1 FROM messages u WHERE u.session_id=m.session_id AND u.role='user'
                AND u.active=1 AND u.id>? AND u.id<m.id)"""
            params.extend((session, user_row, user_row))
        rows = db.execute(
            """SELECT m.id,m.session_id,substr(m.content,1,10000) AS receipt FROM messages_fts f JOIN messages m ON m.id=f.rowid
            WHERE messages_fts MATCH ? AND m.active=1 AND m.role='tool' AND m.tool_name='research_brief'"""
            + criteria
            + " LIMIT ?",
            (*params, MAX_RECEIPTS + 1),
        ).fetchall()
        if len(rows) > MAX_RECEIPTS:
            raise ValueError(
                "Too many matching export receipts to establish the authoring turn."
            )
        turns = {}
        receipt_seen = False
        for r in rows:
            receipt = parsed(r["receipt"])
            if (
                not isinstance(receipt, dict)
                or receipt.get("path") != path
                or receipt.get("revision") != doc["revision"]
            ):
                continue
            receipt_seen = True
            turn = authoring_turn(db, r["session_id"], r["id"], project, doc, path)
            if turn and (user_row is None or turn["turn"] == user_row):
                turns[(turn["session"], turn["turn"])] = turn
        if session is not None and not receipt_seen:
            raise AwaitingExportReceipt(
                "The authoring turn has no persisted export receipt yet."
            )
        if len(turns) != 1:
            raise ValueError(
                "No unique saved turn both authored and exported this revision with verified tool receipts."
            )
        return next(iter(turns.values()))


def citations(db, project, doc, current):
    targets = []
    checked = {}
    for row in doc["rows"]:
        for col, cell in row["cells"].items():
            if not cell.get("edition"):
                continue
            if len(targets) >= MAX_CITATIONS:
                raise ValueError(
                    f"Choose an export with at most {MAX_CITATIONS} cited cells."
                )
            edition = cell["edition"]
            if edition not in checked:
                record = library.accessible_edition(
                    db, project, source=cell["source"], edition=edition
                )
                if search_trials.digest(record["body"]) != record["digest"]:
                    raise ValueError("A cited edition differs from its stored receipt.")
                checked[edition] = record
            s = checked[edition]
            if (
                not cell.get("quote")
                or cell["source"] != s["source"]
                or cell["quote"] not in s["body"]
            ):
                raise ValueError(
                    "A cited passage could not be verified against its source."
                )
            targets.append(
                dict(
                    row=row["name"],
                    column=col,
                    title=s["title"],
                    edition=edition,
                    source=s["source"],
                    digest=s["digest"],
                    quote=cell["quote"],
                    _body=s["body"],
                    current=edition in current,
                )
            )
    if not targets:
        raise ValueError("This export has no cited passages to compare.")
    return targets


def surfaced(rows, target, field):
    return any(
        isinstance(s, dict)
        and s.get("source") == target["source"]
        and s.get("edition") == target["edition"]
        and s.get("digest") == target["digest"]
        and s.get("evidence_scope") != "edition receipt mismatch"
        and isinstance(s.get(field), str)
        and (
            target["quote"] in s[field]
            or (
                field == "excerpt"
                and target["quote"] in search_focus.matched_text(s, target["_body"])
            )
        )
        for s in rows
    )


def check(db, project, args, *, session=None, user_row=None):
    from agent.redact import redact_for_egress

    brief, revision = args.get("brief"), args.get("revision")
    if any(type(n) is not int or n < 1 for n in (brief, revision)):
        raise ValueError("Choose a positive brief number and a saved revision.")
    doc = briefs.get(db, project, brief, revision)
    status = evidence.export_status(brief, revision, doc)
    if status["status"] != "Verified export":
        raise ValueError(
            "The saved CSV must match this exact brief revision. " + status["status"]
        )
    roster = search_trials.collection(db, project)
    if len(roster) > search_trials.MAX_EDITIONS:
        raise ValueError("This library exceeds the replay collection limit.")
    targets = citations(db, project, doc, {s["edition"] for s in roster})
    turn = original(project, doc, status["path"], session=session, user_row=user_row)
    replies = {"original": [], "current": [], "focused": []}
    queries = []
    for query, observed, stamp in turn["searches"]:
        queries.append(redact_for_egress(query))
        replies["original"].extend(observed)
        for arm in ("current", "focused"):
            result = library.search(
                db, project, {"query": query}, focused=arm == "focused"
            )
            replies[arm].extend(result["sources"])
    for t in targets:
        t["search"] = {
            arm: surfaced(rows, t, "excerpt") for arm, rows in replies.items()
        }
        t["original_read"] = surfaced(turn["reads"], t, "text")
        t["quote_digest"] = search_trials.digest(t.pop("quote"))
        t.pop("_body")
    result = dict(
        brief=brief,
        revision=revision,
        title=doc["title"],
        file=status["path"],
        digest=status["digest"],
        collection=search_trials.fingerprint(roster),
        version=search_focus.VERSION,
        turn=turn["turn"],
        session_tag=search_trials.digest(turn["session"]),
        queries=queries,
        later_searches=turn["later_searches"],
        editions={
            arm: len(
                {
                    s["edition"]
                    for s in rows
                    if isinstance(s, dict) and type(s.get("edition")) is int
                }
            )
            for arm, rows in replies.items()
        },
        cells=targets,
    )

    if any(stamp is not None for _,_,stamp in turn["searches"]):
        result["field_checks"]=[stamp for _,_,stamp in turn["searches"]]
        result["source_reads"]=len(turn["reads"])
        result["other_authoring_tools"]=turn["other_tools"]

    encoded = json.dumps(result, sort_keys=True, ensure_ascii=False)
    signature = search_trials.digest(encoded)
    db.execute(
        """INSERT OR IGNORE INTO search_export_checks(project,brief,revision,fingerprint,result,created)
        VALUES(?,?,?,?,?,?)""",
        (project, brief, revision, signature, encoded, int(time.time())),
    )
    ident = db.execute(
        "SELECT id FROM search_export_checks WHERE project=? AND fingerprint=?",
        (project, signature),
    ).fetchone()[0]
    return {"id": ident, "result": result}


def next_check(cells):
    """Prioritize observable losses over gains; a citation without a receipt is unscored."""
    observed = [
        c
        for c in cells
        if c["current"] and (c["search"]["original"] or c["original_read"])
    ]
    alternatives = (
        (
            "regression",
            "A passage the author obtained disappears under focused search. Test whether a fresh job still saves the supported result.",
            [
                c
                for c in observed
                if c["search"]["current"] and not c["search"]["focused"]
            ],
        ),
        (
            "gain",
            "Focused search surfaces a previously used passage that standard search misses. Check whether a fresh job needs fewer reads to save the same result.",
            [
                c
                for c in observed
                if c["search"]["focused"] and not c["search"]["current"]
            ],
        ),
        (
            "shared gap",
            "Both searches miss a passage the original author obtained. Inspect the original query and source before changing the search.",
            [
                c
                for c in observed
                if not c["search"]["focused"] and not c["search"]["current"]
            ],
        ),
        (
            "edition changed",
            "An original citation falls outside the current collection. Compare editions before interpreting a retrieval difference.",
            [c for c in cells if not c["current"]],
        ),
        (
            "unverified origin",
            "The saved citation is real, but its source passage did not appear in the original search or read receipts. Reproduce how the evidence was obtained.",
            [
                c
                for c in cells
                if not c["search"]["original"] and not c["original_read"]
            ],
        ),
    )
    for priority, (kind, reason, chosen) in enumerate(alternatives):
        if chosen:
            return dict(
                priority=priority,
                kind=kind,
                reason=reason,
                cells=[
                    dict(row=c["row"], column=c["column"], edition=c["edition"])
                    for c in chosen
                ],
            )
    return dict(
        priority=len(alternatives),
        kind="tie",
        reason="Both searches preserved the cited passages that could be checked.",
        cells=[],
    )


def scan(db, project, args):
    """Check one page of recent saved jobs. Only the operator can request another page."""
    before = args.get("before")
    if before is not None and (type(before) is not int or before < 1):
        raise ValueError("Select the next valid group of saved briefs.")
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='briefs'").fetchone()
    rows = (
        db.execute(
            """SELECT id,title,revision FROM briefs WHERE project=? AND (? IS NULL OR id<?)
        ORDER BY id DESC LIMIT 4""",
            (project, before, before),
        ).fetchall()
        if exists
        else []
    )
    entries = []
    for row in rows[:3]:
        entry = dict(brief=row["id"], title=row["title"], revision=row["revision"])
        try:
            checked = check(db, project, entry)
        except ValueError as error:
            entry["reason"] = str(error)
        else:
            entry["check"] = checked["id"]
            entry["finding"] = next_check(checked["result"]["cells"])
        entries.append(entry)
    valid = [e for e in entries if e.get("finding") and e["finding"]["kind"] != "tie"]
    chosen = min(valid, key=lambda e: e["finding"]["priority"]) if valid else None
    return dict(
        entries=entries,
        recommended=chosen["check"] if chosen else None,
        before=rows[2]["id"] if len(rows) > 3 else None,
    )
