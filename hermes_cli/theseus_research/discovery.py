"""Explicit cross-project search and immutable edition references."""

from difflib import SequenceMatcher
from itertools import accumulate
import hashlib
import re
from .library import field


def reviewed_pair(db, project, args):
    """Bind preview and adoption to one pin, origin pointer and review revision."""
    source, previous, chosen = (args.get(k) for k in ("source", "previous", "chosen"))
    if any(type(n) is not int or n <= 0 for n in (source, previous, chosen)) or previous == chosen:
        raise ValueError("Select two different editions of a reused source.")
    pair = db.execute("""SELECT s.title,s.project AS owner,old.body AS old_body,new.body AS new_body,
            old.digest AS old_digest,new.digest AS new_digest,
            COALESCE((SELECT max(h.id) FROM research_pin_changes h
                      WHERE h.project=p.project AND h.source=s.id),0) AS revision
        FROM research_pins p JOIN sources s ON s.id=p.source
        JOIN editions old ON old.id=p.edition AND old.source=s.id
        JOIN editions new ON new.id=s.current_id AND new.source=s.id
        WHERE p.project=? AND s.project<>p.project AND s.id=? AND p.edition=? AND s.current_id=?""",
        (project, source, previous, chosen)).fetchone()
    if not pair:
        raise ValueError("The selected reference or the original source changed. Refresh the references and compare again.")
    for side in ("old", "new"):
        if hashlib.sha256(pair[side + "_body"].encode()).hexdigest() != pair[side + "_digest"]:
            raise ValueError("The stored passage does not match its edition receipt.")
    return pair


def change_ranges(old, new):
    """Line alignment is bounded. A large document can always be read as full text."""
    if max(old.count("\n"),new.count("\n")) > 2200:
        return None
    left, right = old.splitlines(keepends=True), new.splitlines(keepends=True)
    if max(len(left), len(right)) > 2200 or len(left)*len(right) > 1_000_000:
        return None
    a = list(accumulate(map(len,left),initial=0))
    b = list(accumulate(map(len,right),initial=0))
    # Identical boilerplate can bridge real changes. Keep those lines as anchors.
    edits = []
    for tag,i,j,k,l in SequenceMatcher(None,left,right,autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        x,y,u,v = a[i],a[j],b[k],b[l]
        while x < y and u < v and old[x] == new[u]:
            x += 1
            u += 1
        while x < y and u < v and old[y-1] == new[v-1]:
            y -= 1
            v -= 1
        edits.append((x,y,u,v))
    return edits


def marked_passage(text, first, last):
    # Use actual text fragments. Browser string offsets count UTF-16 code units.
    start = max(0, first-150)
    previous_break = text.rfind("\n",start,first)
    if previous_break >= 0:
        start = previous_break+1
    end = min(len(text),last+150,start+2400)
    next_break = text.find("\n",last,end)
    if next_break >= 0:
        end = next_break
    before = ("…" if start and text[start-1] != "\n" else "") + text[start:first]
    after = text[min(end,last):end] + ("…" if end<len(text) and text[end]!="\n" and end>=last else "")
    return {"start":start, "before":before, "changed":text[first:min(end,last)],
            "after":after, "continues":last>end, "empty":last==first}


def review_pin(db, project, args):
    db.execute("BEGIN IMMEDIATE" if args["action"] == "discovery_adopt" else "BEGIN")
    if args["action"] == "discovery_adopt" and type(args.get("revision")) is int:
        # A response can be lost after commit. Accept an exact replay of the latest transition.
        recent = db.execute("""SELECT id,previous,chosen FROM research_pin_changes WHERE project=?
            AND source=? ORDER BY id DESC LIMIT 2""", (project, args.get("source"))).fetchall()
        if (recent and recent[0]["previous"] == args.get("previous") and recent[0]["chosen"] == args.get("chosen")
                and args["revision"] == (recent[1]["id"] if len(recent) == 2 else 0)):
            return
    pair = reviewed_pair(db, project, args)
    if args["action"] == "discovery_adopt":
        if type(args.get("revision")) is not int or args["revision"] != pair["revision"]:
            raise ValueError("The reference changed after review. Refresh the references and compare again.")
        db.execute("UPDATE research_pins SET edition=? WHERE project=? AND source=?",
                   (args["chosen"], project, args["source"]))
        db.execute("INSERT INTO research_pin_changes(project,source,previous,chosen) VALUES(?,?,?,?)",
                   (project, args["source"], args["previous"], args["chosen"]))
        return True
    # Start at the first difference, even when thousands of characters match.
    old, new = pair["old_body"], pair["new_body"]
    at = next((i for i, (a, b) in enumerate(zip(old, new)) if a != b), min(len(old), len(new)))
    start = args.get("start", max(0, at-220))
    if type(start) is not int or not 0 <= start <= max(len(old),len(new)):
        raise ValueError("Select a valid source text offset.")
    end = start + 2400
    ranges = change_ranges(old,new)
    focus = args.get("change", 0 if "start" not in args and ranges else None)
    if focus is not None and (type(focus) is not int or not ranges or not 0 <= focus < len(ranges)):
        raise ValueError("Select a changed passage from this comparison.")
    marked = None
    if focus is not None:
        x,y,u,v = ranges[focus]
        marked = {"old":marked_passage(old,x,y), "new":marked_passage(new,u,v)}
    return {"changes":len(ranges) if ranges is not None else None,"change":focus,"marked":marked,
            "title":pair["title"], "owner":pair["owner"], "source":args["source"],
            "previous":args["previous"], "chosen":args["chosen"], "revision":pair["revision"],
            "start":start, "first_difference":at, "old_text":old[start:end], "new_text":new[start:end],
            "old_length":len(old), "new_length":len(new), "next":end if end < max(len(old),len(new)) else None}


def operate(db, project, args):
    action = args["action"]
    impact = None
    if action == "discovery_search":
        query = field(args, "query", 500)
        words = list(dict.fromkeys(re.findall(r"[^\W_]+", query)))[:20]
        if not words:
            raise ValueError("Search for a word or phrase.")
        expr = " OR ".join('"' + w + '"' for w in words)
        rows = db.execute(
            """SELECT s.id AS source,s.title,s.project AS origin_project,s.kind,s.origin,e.id AS edition,e.created,
          snippet(source_search,1,'','','…',22) AS excerpt FROM source_search JOIN sources s ON s.id=source_search.rowid
          JOIN editions e ON s.current_id=e.id WHERE source_search MATCH ? AND s.project<>?
          ORDER BY bm25(source_search,3,1),s.id LIMIT 30""",
            (expr, project),
        )
        return {"matches": [dict(r) for r in rows]}
    if action == "discovery_compare":
        return review_pin(db, project, args)
    if action == "discovery_adopt" and review_pin(db, project, args):
        from .source_impact import observe
        impact=observe(db,project,args["source"],args["chosen"])
    elif action == "discovery_pin":
        r = db.execute(
            """SELECT s.id,e.id FROM sources s JOIN editions e ON e.source=s.id
            WHERE s.id=? AND e.id=? AND s.project=? AND s.project<>?""",
            (
                args.get("source"),
                args.get("edition"),
                args.get("origin_project"),
                project,
            ),
        ).fetchone()
        if not r:
            raise ValueError(
                "Select an original source and edition from another project."
            )
        old = db.execute(
            "SELECT edition FROM research_pins WHERE project=? AND source=?",
            (project, r[0]),
        ).fetchone()
        if old and old[0] != r[1]:
            raise ValueError(
                "This project already uses a different edition. Review its existing reference."
            )
        db.execute(
            "INSERT OR IGNORE INTO research_pins(project,source,edition) VALUES(?,?,?)",
            (project, *r),
        )
    elif action not in ("discovery_list","discovery_adopt"):
        raise ValueError("Unknown discovery operation.")
    return {
        **({"affected_briefs":impact} if impact else {}),
        "pins": [
            dict(r)
            for r in db.execute(
                """SELECT p.source,p.edition,p.annotation,p.added,s.title,s.project AS origin_project,
        s.current_id,e.created FROM research_pins p JOIN sources s ON s.id=p.source JOIN editions e ON e.id=p.edition
        WHERE p.project=? ORDER BY p.added DESC,p.source DESC""",
                (project,),
            )
        ]
    }
