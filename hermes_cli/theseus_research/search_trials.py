"""Bounded comparisons of library tools. Judgments stay outside the agent tool surface."""

import hashlib
import json
import time

from . import library, search_focus

MAX_LOOKUPS = 8
WINDOW_SECONDS = 15 * 60
MAX_EDITIONS = 200
ROLES = {"required", "context", "unrelated"}
EXPORT_CHECK_DDL = """CREATE TABLE IF NOT EXISTS search_export_checks(
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, brief INTEGER NOT NULL, revision INTEGER NOT NULL,
 fingerprint TEXT NOT NULL, result TEXT NOT NULL, created INTEGER NOT NULL,
 UNIQUE(project,fingerprint))"""
CHOICE_DDL = """CREATE TABLE IF NOT EXISTS research_search_choices(
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, version TEXT, created INTEGER NOT NULL)"""
DDL = f"""
CREATE TABLE IF NOT EXISTS search_trial_cases(
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL, question TEXT NOT NULL,
 collection TEXT NOT NULL, judgments TEXT NOT NULL, created INTEGER NOT NULL,
 UNIQUE(project,title));
CREATE TABLE IF NOT EXISTS search_trial_windows(
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, case_id INTEGER NOT NULL REFERENCES search_trial_cases(id),
 arm TEXT NOT NULL, version TEXT NOT NULL, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0,
 session_tag TEXT, ended TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS search_one_window ON search_trial_windows(project) WHERE ended IS NULL;
{CHOICE_DDL};
CREATE INDEX IF NOT EXISTS search_choice_project ON research_search_choices(project,id);
{EXPORT_CHECK_DDL};
CREATE INDEX IF NOT EXISTS search_check_project ON search_export_checks(project,id);
CREATE TABLE IF NOT EXISTS search_trial_pairs(
 id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL REFERENCES search_trial_cases(id),
 window_id INTEGER REFERENCES search_trial_windows(id), query TEXT NOT NULL, version TEXT NOT NULL,
 results TEXT NOT NULL, created INTEGER NOT NULL);
"""


def digest(data):
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def collection(db, project):
    rows = db.execute(
        """SELECT s.id AS source,s.title,e.id AS edition,e.digest FROM sources s
        JOIN editions e ON s.current_id=e.id WHERE s.project=?
        UNION ALL SELECT s.id,s.title,e.id,e.digest FROM research_pins p
        JOIN sources s ON s.id=p.source JOIN editions e ON e.id=p.edition
        WHERE p.project=? ORDER BY source,edition LIMIT ?""",
        (project, project, MAX_EDITIONS + 1),
    )
    return [dict(row) for row in rows]


def fingerprint(sources):
    return digest(json.dumps(sources, sort_keys=True, ensure_ascii=False))


def freeze(db, project, args, sources):
    if len(sources) > MAX_EDITIONS:
        raise ValueError(
            f"Choose a collection with at most {MAX_EDITIONS} editions for a bounded trial."
        )
    roster = args.get("judgments")
    if not isinstance(roster, list) or not 1 <= len(roster) <= len(sources):
        raise ValueError("Judge at least one edition in the current collection.")
    current = {r["edition"]: r for r in sources}
    labels = {}
    for record in roster:
        if not isinstance(record, dict):
            raise ValueError("Each judgment needs an edition and a role.")
        edition, role = record.get("edition"), record.get("role")
        if (
            type(edition) is not int
            or edition not in current
            or str(edition) in labels
            or not isinstance(role, str)
            or role not in ROLES
        ):
            raise ValueError("Choose each current edition once with a supported role.")
        multiple = record.get("passages")
        quote = library.field(
            record, "quote", 2400, required=role == "required" and multiple is None
        )
        passages = [quote] if quote else []
        if multiple is not None:
            if (
                role != "required"
                or not isinstance(multiple, list)
                or not 1 <= len(multiple) <= 8
            ):
                raise ValueError(
                    "Choose one to eight distinct required passages from this edition."
                )
            passages = [library.field({"quote": v}, "quote", 2400) for v in multiple]
            if len(set(passages)) != len(passages) or (quote and quote != passages[0]):
                raise ValueError(
                    "Required passages must be distinct and agree with the first selected passage."
                )
            quote = passages[0]
        if passages:
            source = library.accessible_edition(db, project, edition=edition)
            if digest(source["body"]) != source["digest"] or any(
                q not in source["body"] for q in passages
            ):
                raise ValueError(
                    "The selected passage must occur in the verified edition."
                )
        labels[str(edition)] = {"role": role, "quote": quote}
        if len(passages) > 1:
            labels[str(edition)]["passages"] = passages
    title = library.field(args, "title", 180)
    question = library.field(args, "question", 500)
    encoded = json.dumps(labels, sort_keys=True, ensure_ascii=False)
    mark = fingerprint(sources)
    previous = db.execute(
        "SELECT * FROM search_trial_cases WHERE project=? AND title=?", (project, title)
    ).fetchone()
    if previous:
        if (previous["question"], previous["collection"], previous["judgments"]) != (
            question,
            mark,
            encoded,
        ):
            raise ValueError(
                "That case is already frozen. Give the changed case a different title."
            )
        return previous["id"]
    return db.execute(
        """INSERT INTO search_trial_cases(project,title,question,collection,judgments,created)
                      VALUES(?,?,?,?,?,?)""",
        (project, title, question, mark, encoded, int(time.time())),
    ).lastrowid


def summarize(reply, labels):
    required = {
        (int(key), q)
        for key, record in labels.items()
        if record["role"] == "required"
        for q in record.get("passages", [record["quote"]])
    }
    received = set()
    items = []
    for row in reply["sources"]:
        edition = row["edition"]
        label = labels.get(str(edition), {})
        role = label.get("role", "unjudged")
        excerpt = row["excerpt"]
        extra = search_focus.matched_text(row)
        quotes = (
            label.get("passages", [label.get("quote")]) if role == "required" else []
        )
        hits = [
            q
            for q in quotes
            if row["evidence_scope"] != "edition receipt mismatch"
            and (q in excerpt or q in extra)
        ]
        received.update((edition, q) for q in hits)
        items.append(
            dict(
                edition=edition,
                title=row["title"],
                source=row["source"],
                digest=row["digest"],
                role=role,
                passage=bool(quotes) and len(hits) == len(quotes),
                passages_returned=len(hits),
                passages_required=len(quotes),
                bytes=len(excerpt.encode()) + len(extra.encode()),
                scope=row["evidence_scope"],
            )
        )
    return dict(
        sources=items,
        required=len(required),
        passages=len(received),
        missing=sorted({edition for edition, _ in required - received}),
        unrelated=sum(x["role"] == "unrelated" for x in items),
        unjudged=sum(x["role"] == "unjudged" for x in items),
        excerpt_bytes=sum(x["bytes"] for x in items),
        extent=reply["coverage"]["extent"],
    )


def compare(db, case, args, window=None):
    from agent.redact import redact_for_egress

    query = library.field(args, "query", 500, required=False)
    labels = json.loads(case["judgments"])
    for edition, label in labels.items():
        if label["role"] == "required":
            source = library.accessible_edition(
                db, case["project"], edition=int(edition)
            )
            if digest(source["body"]) != source["digest"] or any(
                q not in source["body"] for q in label.get("passages", [label["quote"]])
            ):
                raise ValueError("The frozen passage differs from its edition receipt.")
    replies = {
        arm: library.search(
            db, case["project"], {"query": query}, focused=arm == "focused"
        )
        for arm in ("current", "focused")
    }
    pair = {arm: summarize(reply, labels) for arm, reply in replies.items()}
    result = db.execute(
        """INSERT INTO search_trial_pairs(case_id,window_id,query,version,results,created)
        VALUES(?,?,?,?,?,?)""",
        (
            case["id"],
            window["id"] if window else None,
            redact_for_egress(query),
            search_focus.VERSION,
            json.dumps(pair),
            int(time.time()),
        ),
    ).lastrowid
    return replies[window["arm"]] if window else dict(pair=result, results=pair)


def project_choice(db, project):
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='research_search_choices'"
    ).fetchone():
        row = db.execute(
            "SELECT id,version FROM research_search_choices WHERE project=? ORDER BY id DESC LIMIT 1",
            (project,),
        ).fetchone()
        if row:
            return dict(
                id=row["id"],
                version=row["version"],
                focused=row["version"] == search_focus.VERSION,
            )
    return dict(id=0, version=None, focused=False)


def configure(db, project, args):
    choice = project_choice(db, project)
    if (
        args.get("arm") not in ("current", "focused")
        or type(args.get("expected")) is not int
    ):
        raise ValueError(
            "Choose a search setting using the displayed project decision."
        )
    version = search_focus.VERSION if args["arm"] == "focused" else None
    if version != choice["version"]:
        if args["expected"] != choice["id"]:
            raise ValueError(
                "The project search setting changed. Refresh before changing it."
            )
        db.execute(
            "INSERT INTO research_search_choices(project,version,created) VALUES(?,?,?)",
            (project, version, int(time.time())),
        )
    return project_choice(db, project)


def agent_search(db, project, args, session_id):
    from .search_followup import ordinary_search
    if (
        not session_id
        or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='search_trial_windows'"
        ).fetchone()
    ):
        return ordinary_search(db,project,args,session_id)
    window = db.execute(
        "SELECT id FROM search_trial_windows WHERE project=? AND ended IS NULL",
        (project,),
    ).fetchone()
    if not window:
        return ordinary_search(db,project,args,session_id)
    db.execute("BEGIN IMMEDIATE")
    window = db.execute(
        "SELECT * FROM search_trial_windows WHERE id=? AND ended IS NULL",
        (window["id"],),
    ).fetchone()
    if not window:
        return ordinary_search(db,project,args,session_id)
    case = db.execute(
        "SELECT * FROM search_trial_cases WHERE id=?", (window["case_id"],)
    ).fetchone()
    invalid = end_reason(window, case, collection(db, project))
    if invalid:
        db.execute(
            "UPDATE search_trial_windows SET ended=? WHERE id=?",
            (invalid, window["id"]),
        )
        return ordinary_search(db,project,args,session_id)
    tag = digest(session_id)
    if window["session_tag"] and window["session_tag"] != tag:
        return ordinary_search(db,project,args,session_id)
    reply = compare(db, case, args, window)
    db.execute(
        """UPDATE search_trial_windows SET session_tag=?,used=used+1,
               ended=CASE WHEN used+1>=? THEN 'Lookup budget reached' ELSE NULL END WHERE id=?""",
        (tag, MAX_LOOKUPS, window["id"]),
    )
    return reply


def end_reason(window, case, sources):
    if window["expires"] <= int(time.time()):
        return "Time window ended"
    if window["version"] != search_focus.VERSION:
        return "Tool version changed"
    if case["collection"] != fingerprint(sources):
        return "Library changed"
    return None


def operate(db, project, args):
    db.executescript(DDL)
    db.execute("BEGIN IMMEDIATE")
    sources = collection(db, project)
    mark = fingerprint(sources)
    action = args["action"]
    window = db.execute(
        "SELECT * FROM search_trial_windows WHERE project=? AND ended IS NULL",
        (project,),
    ).fetchone()
    if window:
        active_case = db.execute(
            "SELECT * FROM search_trial_cases WHERE id=?", (window["case_id"],)
        ).fetchone()
        end = end_reason(window, active_case, sources)
        if end:
            db.execute(
                "UPDATE search_trial_windows SET ended=? WHERE id=?",
                (end, window["id"]),
            )
            window = None
    if action == "search_trial_configure":
        return configure(db, project, args)
    if action == "search_trial_scan_exports":
        from . import search_exports

        return search_exports.scan(db, project, args)
    if action == "search_trial_export_check":
        from . import search_exports

        return search_exports.check(db, project, args)
    if action == "search_trial_freeze":
        return {"case": freeze(db, project, args, sources)}
    if action == "search_trial_stop":
        if window and window["id"] == args.get("window"):
            db.execute(
                "UPDATE search_trial_windows SET ended='Operator stopped' WHERE id=?",
                (window["id"],),
            )
        return {"stopped": bool(window and window["id"] == args.get("window"))}
    if action in ("search_trial_replay", "search_trial_arm"):
        case = db.execute(
            "SELECT * FROM search_trial_cases WHERE id=? AND project=?",
            (args.get("case"), project),
        ).fetchone()
        if not case or case["collection"] != mark:
            raise ValueError("Choose a frozen case that still matches this library.")
        if action == "search_trial_replay":
            return compare(db, case, {"query": args.get("query", case["question"])})
        arm = args.get("arm")
        if arm not in ("focused", "current"):
            raise ValueError("Choose a supported search variant.")
        if window:
            if window["case_id"] == case["id"] and window["arm"] == arm:
                return {"window": window["id"]}
            raise ValueError("End the current search trial before starting another.")
        wid = db.execute(
            """INSERT INTO search_trial_windows(project,case_id,arm,version,expires)
            VALUES(?,?,?,?,?)""",
            (
                project,
                case["id"],
                arm,
                search_focus.VERSION,
                int(time.time()) + WINDOW_SECONDS,
            ),
        ).lastrowid
        return {"window": wid}
    if action != "search_trial_list":
        raise ValueError("Unknown search experiment action.")
    cases = []
    for case in db.execute(
        "SELECT id,title,question,collection FROM search_trial_cases WHERE project=? ORDER BY id DESC LIMIT 20",
        (project,),
    ):
        pairs = []
        if case["id"] == args.get("case") or (not args.get("case") and not cases):
            for row in db.execute(
                """SELECT p.id,p.query,p.results,p.version,w.arm FROM search_trial_pairs p
                LEFT JOIN search_trial_windows w ON w.id=p.window_id WHERE p.case_id=? ORDER BY p.id DESC LIMIT 10""",
                (case["id"],),
            ):
                pairs.append({**dict(row), "results": json.loads(row["results"])})
        cases.append(
            dict(
                id=case["id"],
                title=case["title"],
                question=case["question"],
                current=case["collection"] == mark,
                pairs=pairs,
            )
        )
    briefs = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='briefs'").fetchone():
        briefs = [
            dict(row)
            for row in db.execute(
                "SELECT id,title,revision FROM briefs WHERE project=? ORDER BY id DESC LIMIT 100",
                (project,),
            )
        ]
    checks = [
        dict(id=r["id"], created=r["created"], result=json.loads(r["result"]))
        for r in db.execute(
            "SELECT id,created,result FROM search_export_checks WHERE project=? ORDER BY id DESC LIMIT 10",
            (project,),
        )
    ]
    selected_check = args.get("check")
    if (
        type(selected_check) is int
        and selected_check > 0
        and all(c["id"] != selected_check for c in checks)
    ):
        saved = db.execute(
            "SELECT id,created,result FROM search_export_checks WHERE project=? AND id=?",
            (project, selected_check),
        ).fetchone()
        if saved:
            checks.append(
                dict(
                    id=saved["id"],
                    created=saved["created"],
                    result=json.loads(saved["result"]),
                )
            )
    return dict(
        policy=project_choice(db, project),
        policy_history=[
            dict(r)
            for r in db.execute(
                "SELECT id,version,created FROM research_search_choices WHERE project=? ORDER BY id DESC LIMIT 5",
                (project,),
            )
        ],
        briefs=briefs,
        checks=checks,
        cases=cases,
        sources=sources[:MAX_EDITIONS],
        oversize=len(sources) > MAX_EDITIONS,
        window=dict(
            id=window["id"],
            case=window["case_id"],
            arm=window["arm"],
            used=window["used"],
            expires=window["expires"],
        )
        if window
        else None,
        limit=MAX_LOOKUPS,
        minutes=WINDOW_SECONDS // 60,
    )
