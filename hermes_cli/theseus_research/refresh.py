"""Compare cited source editions without replacing evidence collected under different conditions."""
import csv
import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path

from .briefs import DDL, get, store
from .library import field

PAGE_SIZE = 24
REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS citation_refresh_decisions (
    id INTEGER PRIMARY KEY, brief INTEGER NOT NULL REFERENCES briefs(id),
    row_name TEXT NOT NULL, column_name TEXT NOT NULL,
    old_edition INTEGER NOT NULL REFERENCES editions(id),
    new_edition INTEGER NOT NULL REFERENCES editions(id),
    claim_digest TEXT NOT NULL, choice TEXT NOT NULL CHECK(choice IN ('keep','reopen')),
    reason TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS citation_refresh_latest
ON citation_refresh_decisions(brief,row_name,column_name,old_edition,new_edition,claim_digest,id);
"""


class Records:
    """Unique keyed rows and their original citation text, including quoted line breaks."""

    def __init__(self, text, delimiter, key_name=None):
        lines = io.StringIO(text, newline="").readlines()
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        self.columns = next(reader, [])
        self.rows = {}
        self.valid = bool(self.columns) and all(self.columns) and len(set(self.columns)) == len(self.columns)
        if not self.valid or (key_name is not None and key_name not in self.columns):
            self.valid = False
            return
        key_index = self.columns.index(key_name) if key_name is not None else 0
        start = reader.line_num
        for record in reader:
            literal = "".join(lines[start:reader.line_num]).rstrip("\r\n")
            start = reader.line_num
            if len(record) <= key_index or not record[key_index].strip():
                continue
            key = record[key_index]
            if key in self.rows or len(record) != len(self.columns):
                self.rows[key] = None
            else:
                self.rows[key] = (record, literal)


def passage_windows(text, quote, radius=200):
    """Keep literal source context for at most two occurrences, including overlapping matches."""
    matches = []
    start = text.find(quote) if quote else -1
    while start >= 0 and len(matches) < 2:
        end = start + len(quote)
        matches.append(dict(offset=start, before=text[max(0,start-radius):start], after=text[end:end+radius]))
        start = text.find(quote, start + 1)
    return {"matches":matches, "more":start >= 0}


class EditionComparison:
    def __init__(self, src):
        self.src = src
        self.tables = None
        self.suffix = Path(src["origin"]).suffix.lower()
        if self.suffix in (".csv", ".tsv"):
            self.delimiter = "\t" if self.suffix == ".tsv" else ","
            try:
                old = Records(src["old"], self.delimiter)
                if old.valid:
                    new = Records(src["new"], self.delimiter, old.columns[0])
                    if new.valid and set(old.columns) == set(new.columns):
                        self.tables = old, new
            except csv.Error:
                pass

    def classify(self, cell):
        result = dict(state="review", value=None, quote=None, context_changes=[])
        if self.suffix not in (".csv", ".tsv"):
            prior = passage_windows(self.src["old"], cell["quote"])
            current = passage_windows(self.src["new"], cell["quote"])
            result["nearby"] = {"prior":prior, "current":current, "radius":200}
            hits = current["matches"]
            if hits:
                result.update(state="passage retained", quote=cell["quote"])
                if len(hits)>1 or len(prior["matches"])!=1:
                    result["state"] = "passage repeated"
                elif any(" ".join(hits[0][side].split()) != " ".join(prior["matches"][0][side].split())
                         for side in ("before","after")):
                    result["state"] = "nearby wording changed"
            return result
        if not self.tables:
            return result
        old, new = self.tables
        try:
            citation = list(csv.reader(io.StringIO(cell["quote"], newline=""), delimiter=self.delimiter, strict=True))
        except csv.Error:
            return result
        if len(citation) != 1 or not citation[0]:
            return result
        record = citation[0]
        key = record[0]
        original, candidate = old.rows.get(key), new.rows.get(key)
        if not original or original[0] != record or not candidate or record.count(cell["value"]) != 1:
            return result
        new_row, literal = candidate
        if not literal or len(literal) > 2400:
            return result
        column = old.columns[record.index(cell["value"])]
        by_name = dict(zip(new.columns, new_row))
        value = by_name[column]
        context = [
            {"field": label, "before": previous, "after": by_name[label]}
            for label, previous in zip(old.columns, record)
            if label != column and previous != by_name[label]
        ]
        if context:
            state = "context changed"
        elif not value.strip():
            state = "review"
        else:
            state = "unchanged" if value == cell["value"] else "changed"
        return dict(state=state, value=value, quote=literal, context_changes=context)


def decision_key(brief, row, column, cell, new_edition):
    text = json.dumps([cell["value"], cell["quote"], cell.get("note", "")], ensure_ascii=False)
    return (brief, row, column, cell["edition"], new_edition, hashlib.sha256(text.encode()).hexdigest())


def recent_decision(db, key):
    last = db.execute("""SELECT id,choice,reason,created FROM citation_refresh_decisions
        WHERE brief=? AND row_name=? AND column_name=? AND old_edition=? AND new_edition=? AND claim_digest=?
        ORDER BY id DESC LIMIT 1""", key).fetchone()
    return dict(last) if last else None


def history_page(db, brief, row, column, before=None):
    if before is not None and (type(before) is not int or before < 1):
        raise ValueError("Use the review history cursor returned by the previous page.")
    events = db.execute("""SELECT id,choice,reason,created,old_edition,new_edition
        FROM citation_refresh_decisions WHERE brief=? AND row_name=? AND column_name=?
        AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 13""",
        (brief, row, column, before, before)).fetchall()
    return {"decisions":[dict(event) for event in events[:12]],
            "next_before":events[11]["id"] if len(events)>12 else None}


def coverage(db, project):
    groups = dict(db.execute("""
        SELECT CASE WHEN e.id IS NULL AND json_extract(c.value,'$.edition') IS NULL THEN 'uncited'
                    WHEN s.project=:project AND e.id=s.current_id THEN 'current_local'
                    WHEN s.project=:project THEN 'different_local'
                    WHEN p.edition=e.id THEN 'pinned'
                    WHEN EXISTS(SELECT 1 FROM research_pin_changes h WHERE h.project=:project
                        AND h.source=s.id AND h.previous=e.id) THEN 'previous_pin'
                    ELSE 'unavailable' END AS status, count(*)
          FROM briefs b JOIN brief_versions v ON v.brief=b.id AND v.revision=b.revision
          JOIN json_each(v.payload,'$.rows') r JOIN json_each(r.value,'$.cells') c
          LEFT JOIN editions e ON e.id=json_extract(c.value,'$.edition')
          LEFT JOIN sources s ON s.id=e.source
          LEFT JOIN research_pins p ON p.source=s.id AND p.project=:project
         WHERE b.project=:project AND (coalesce(json_extract(c.value,'$.value'),'')!=''
                                      OR json_extract(c.value,'$.edition') IS NOT NULL)
         GROUP BY status
    """, {"project":project}).fetchall())
    return dict(briefs=db.execute("SELECT count(*) FROM briefs WHERE project=?", (project,)).fetchone()[0],
                populated_cells=sum(groups.values()),
                **({"previous_pin":groups["previous_pin"]} if "previous_pin" in groups else {}),
                **{key:groups.get(key,0) for key in ("uncited","current_local","different_local","pinned","unavailable")})


def compare_for(db, project):
    @lru_cache(maxsize=4)
    def pair(edition):
        src = db.execute("""
            SELECT s.origin,s.title,n.id AS current_id,o.body AS old,n.body AS new
            FROM editions o JOIN sources s ON s.id=o.source
            LEFT JOIN research_pins p ON p.source=s.id AND p.project=:project
            JOIN editions n ON n.id=CASE WHEN s.project=:project THEN s.current_id ELSE p.edition END
            WHERE o.id=:edition AND o.id<>n.id AND (s.project=:project OR EXISTS(
                SELECT 1 FROM research_pin_changes h WHERE h.project=:project AND h.source=s.id AND h.previous=o.id))
        """, {"project":project, "edition":edition}).fetchone()
        return EditionComparison(src) if src else None

    def compare(brief, row, column, cell):
        edition = cell.get("edition")
        comparison = pair(edition) if edition else None
        if not comparison:
            return None
        key = decision_key(brief, row, column, cell, comparison.src["current_id"])
        prior_review = db.execute("""SELECT id,choice,reason,created,new_edition FROM citation_refresh_decisions
            WHERE brief=? AND row_name=? AND column_name=? AND old_edition=? AND new_edition<>? AND claim_digest=?
            ORDER BY id DESC LIMIT 1""", key).fetchone()
        return dict(row=row, column=column, decision=recent_decision(db, key),
                    prior_review=dict(prior_review) if prior_review else None,
                    note=cell.get("note",""), prior=cell["value"], prior_quote=cell["quote"],
                    old_edition=edition, new_edition=comparison.src["current_id"],
                    source_title=comparison.src["title"], **comparison.classify(cell))
    return compare


def changes(db, doc):
    compare = compare_for(db, doc["project"])
    out = []
    for row in doc["rows"]:
        for column, cell in row["cells"].items():
            change = compare(doc["brief"], row["name"], column, cell)
            if change:
                out.append(change)
    return out


def review_project(db, project, args):
    after = args.get("after", [0, 0, ""])
    if (not isinstance(after, list) or len(after) != 3 or
            any(type(n) is not int or n < 0 for n in after[:2]) or not isinstance(after[2], str)):
        raise ValueError("Use the cursor returned by the previous review page.")
    rows = db.execute("""
        SELECT b.id AS brief,b.title,b.revision,json_extract(v.payload,'$.question') AS question,r.key AS position,
               s.id AS source,s.title AS source_title,e.id AS old_id,e.digest AS old_digest,
               n.id AS new_id,n.digest AS new_digest,
               json_extract(r.value,'$.name') AS row_name,c.key AS column_name,c.value AS cell
          FROM briefs b JOIN brief_versions v ON v.brief=b.id AND v.revision=b.revision
          JOIN json_each(v.payload,'$.rows') r JOIN json_each(r.value,'$.cells') c
          JOIN editions e ON e.id=json_extract(c.value,'$.edition')
          JOIN sources s ON s.id=e.source
          LEFT JOIN research_pins p ON p.source=s.id AND p.project=b.project
          JOIN editions n ON n.id=CASE WHEN s.project=b.project THEN s.current_id ELSE p.edition END
         WHERE b.project=? AND e.id<>n.id AND (s.project=b.project OR EXISTS(
               SELECT 1 FROM research_pin_changes h WHERE h.project=b.project AND h.source=s.id AND h.previous=e.id))
           AND (b.id,r.key,c.key)>(?,?,?)
         ORDER BY b.id,r.key,c.key LIMIT ?
    """, (project, *after, PAGE_SIZE + 1)).fetchall()
    compare = compare_for(db, project)
    items = []
    receipts = {}
    for item in rows[:PAGE_SIZE]:
        change = compare(item["brief"], item["row_name"], item["column_name"], json.loads(item["cell"]))
        items.append(dict(brief=item["brief"], title=item["title"], question=item["question"],
                          revision=item["revision"], source=item["source"], **change))
        receipts[(item["old_id"],item["new_id"])] = dict(source=item["source"],title=item["source_title"],
            prior={"edition":item["old_id"],"sha256":item["old_digest"]},
            current={"edition":item["new_id"],"sha256":item["new_digest"]})
    cursor = None
    if len(rows) > PAGE_SIZE:
        last = rows[PAGE_SIZE - 1]
        cursor = [last["brief"], last["position"], last["column_name"]]
    return {"project":project, "coverage":coverage(db,project), "source_receipts":list(receipts.values()),
            "changes":items, "next_after":cursor}


def operate(db, project, args):
    db.executescript(DDL + REVIEW_SCHEMA)
    if args["action"] == "refresh_history":
        doc = get(db, project, args.get("brief"))
        return history_page(db, doc["brief"], field(args,"row",160), field(args,"column",100), args.get("before"))
    if args["action"] == "refresh_review":
        db.execute("BEGIN")
        return review_project(db, project, args)
    action = args["action"]
    apply = action in ("refresh_apply", "refresh_keep", "refresh_reopen")
    db.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
    doc = get(db, project, args.get("brief"))
    options = changes(db, doc)
    if not apply:
        return {"brief": doc["brief"], "revision": doc["revision"], "changes": options}
    if args.get("expected_revision") != doc["revision"]:
        raise ValueError("The brief changed. Compare editions again.")
    change = next(
        (
            x
            for x in options
            if (x["row"], x["column"], x["new_edition"])
            == (args.get("row"), args.get("column"), args.get("new_edition"))
        ),
        None,
    )
    if not change:
        raise ValueError("This comparison changed. Review the current editions again.")
    decision = change["decision"]
    kept = decision is not None and decision["choice"] == "keep"
    cell = next(r for r in doc["rows"] if r["name"] == change["row"])["cells"][change["column"]]
    if action in ("refresh_keep", "refresh_reopen"):
        choice = "keep" if action == "refresh_keep" else "reopen"
        reason = field(args, "reason", 600) if choice == "keep" else ""
        if decision and decision["choice"] == choice and decision["reason"] == reason:
            return {"decision": decision}
        if args.get("expected_decision") != (decision["id"] if decision else None):
            raise ValueError("The review decision changed. Recheck the source comparison.")
        if choice == "reopen" and not kept:
            raise ValueError("This claim is already open for review.")
        key = decision_key(doc["brief"], change["row"], change["column"], cell, change["new_edition"])
        db.execute("""INSERT INTO citation_refresh_decisions
            (brief,row_name,column_name,old_edition,new_edition,claim_digest,choice,reason)
            VALUES(?,?,?,?,?,?,?,?)""", (*key, choice, reason))
        return {"decision": recent_decision(db, key)}
    if kept:
        raise ValueError("This claim was kept with its earlier evidence. Reopen its review before applying a change.")
    if change["state"] == "context changed":
        raise ValueError("The record context changed. Review the two passages before editing this claim.")
    if change["state"] not in ("changed", "unchanged"):
        raise ValueError(
            "There is no uniquely matched field to apply. Review the source and edit this cell manually."
        )
    cell.update(
        edition=change["new_edition"], value=change["value"], quote=change["quote"]
    )
    store(db, doc["brief"], doc["revision"] + 1, doc)
    return get(db, project, doc["brief"])
