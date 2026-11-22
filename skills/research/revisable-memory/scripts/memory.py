"""Scoped project decisions, competing explanations and revision-aware artifact jobs."""

try:
    from hermes_cli import theseus_shadow as shadow
except ModuleNotFoundError as exc:
    if exc.name != "hermes_cli":
        raise
    shadow = None  # The standalone script also runs outside the installed harness.

import argparse
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

TOKEN = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
SLOT = re.compile(r"{{([a-z][a-z0-9_-]{0,79})(?::([a-z][a-z0-9_-]{0,79}))?(@pause|@assessment)?}}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_events(
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, request TEXT NOT NULL, payload TEXT NOT NULL,
 evidence TEXT NOT NULL, citation TEXT NOT NULL, observed TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(project,request));
CREATE TABLE IF NOT EXISTS memory_claims(
 id INTEGER PRIMARY KEY, event INTEGER NOT NULL, project TEXT NOT NULL, concept TEXT NOT NULL,
 scope TEXT NOT NULL, edition INTEGER NOT NULL, value TEXT NOT NULL, supports TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS memory_lookup ON memory_claims(project,concept,scope,edition);
CREATE TABLE IF NOT EXISTS memory_topics(project TEXT, child TEXT, parent TEXT, event INTEGER,
 PRIMARY KEY(project,child));
CREATE TABLE IF NOT EXISTS memory_theses(project TEXT, name TEXT, event INTEGER,
 position TEXT, status TEXT, PRIMARY KEY(project,name,event));
CREATE TABLE IF NOT EXISTS memory_observations(project TEXT, thesis TEXT, event INTEGER, direction TEXT,
 PRIMARY KEY(project,thesis,event));
CREATE TABLE IF NOT EXISTS memory_archive_heads(project TEXT,event INTEGER,activation INTEGER NOT NULL,
 PRIMARY KEY(project,event));
CREATE TABLE IF NOT EXISTS memory_check_plans(project TEXT,event INTEGER,key TEXT,latest_doc INTEGER,
 PRIMARY KEY(project,event,key));
CREATE TABLE IF NOT EXISTS memory_check_results(project TEXT,event INTEGER,plan INTEGER,key TEXT,
 snapshot INTEGER,data TEXT,PRIMARY KEY(project,event,key),UNIQUE(project,plan,key,snapshot));
CREATE TABLE IF NOT EXISTS memory_impact_inventory(event INTEGER PRIMARY KEY, jobs TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_field_scopes(claim INTEGER PRIMARY KEY, fields TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_jobs(project TEXT, name TEXT, edition INTEGER, template TEXT,
 draft TEXT, prepared TEXT, done INTEGER DEFAULT 0, PRIMARY KEY(project,name));
"""


def token(s):
    if not isinstance(s, str) or not TOKEN.fullmatch(s):
        raise ValueError("use a lowercase identifier of up to 80 characters")
    return s


def string(s):
    if not isinstance(s, str) or not s.strip() or len(s) > 20000:
        raise ValueError("expected nonempty text of at most 20000 characters")
    return s


def number(n):
    if type(n) is not int or n < 1:
        raise ValueError("edition or claim ID must be a positive integer")
    return n


def packed(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def chat_source(home, quote):
    uri = (home / "state.db").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as history:
        session = os.environ.get("HERMES_SESSION_ID")
        if not session:
            raise ValueError("a live chat session is required to cite a correction")
        rows = history.execute(
            """SELECT session_id,id FROM messages WHERE role='user'
            AND session_id=? AND instr(content,?)>0 ORDER BY id DESC LIMIT 1""",
            (session, quote),
        ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            "evidence must be an exact excerpt from a saved user message in this chat"
        )
    return f"chat:{rows[0][0]}:message:{rows[0][1]}"


class Memory:
    def __init__(self, home, project):
        self.project = token(project)
        self.root = home / "project-recall"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "records.sqlite", timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    @classmethod
    def from_connection(cls, home, project, db):
        store = cls.__new__(cls)
        store.project = token(project)
        store.root = home / "project-recall"
        store.db = db
        return store

    def rows(self, sql, *args):
        return [dict(row) for row in self.db.execute(sql, (self.project, *args))]

    def parent(self, concept, at):
        rows = self.rows(
            "SELECT parent FROM memory_topics WHERE project=? AND child=? AND event<=?",
            concept,
            at,
        )
        return rows[0]["parent"] if rows else None

    def resolve(self, concept, edition, job="", at=2**63 - 1, visiting=()):
        token(concept)
        number(edition)
        if concept in visiting or len(visiting) > 40:
            raise ValueError("cyclic or excessive claim dependency")
        # An explicit narrower concept supersedes inheritance. An invalidated
        # narrower conclusion requires reconsideration; it cannot silently fall back.
        rows = self.rows(
            """SELECT * FROM memory_claims WHERE project=? AND concept=?
            AND scope IN ('',?) AND edition<=? AND event<=?
            ORDER BY (scope!='') DESC, edition DESC,event DESC,id DESC LIMIT 1""",
            concept,
            job,
            edition,
            at,
        )
        trail = (*visiting, concept)
        if not rows:
            parent = self.parent(concept, at)
            return self.resolve(parent, edition, job, at, trail) if parent else None
        claim = rows[0]
        alternatives = json.loads(claim["supports"])
        for group in alternatives:
            if all(
                (self.governing(ref["concept"], edition, job, at, trail) or {}).get(
                    "id"
                )
                == ref["id"]
                for ref in group
            ):
                return claim
        return None if alternatives else claim

    def governing(self, concept, edition, job="", at=2**63 - 1, visiting=()):
        selected = self.resolve(concept, edition, job, at, visiting)
        if not selected:
            return None
        descendants = self.rows(
            """WITH RECURSIVE tree(child) AS (
            SELECT child FROM memory_topics WHERE project=? AND parent=? AND event<=?
            UNION SELECT t.child FROM memory_topics t JOIN tree ON t.parent=tree.child
            WHERE t.project=? AND t.event<=?) SELECT child FROM tree""",
            concept,
            at,
            self.project,
            at,
        )
        for row in descendants:
            branch = self.resolve(row["child"], edition, job, at, visiting)
            if not branch or branch["value"] != selected["value"]:
                return None
        return selected

    def cite(self, record, quote):
        row = self.db.execute(
            """SELECT d.id,d.digest,s.body FROM spans s JOIN docs d ON d.id=s.doc
            JOIN names n ON n.name=d.name WHERE s.rowid=? AND n.anchor=(
            SELECT anchor FROM names WHERE name=?)""",
            (number(record), self.project),
        ).fetchone()
        if not row or string(quote) not in row["body"]:
            raise ValueError("exact excerpt from this project's source record required")
        return f"record:{record}:snapshot:{row['id']}:sha256:{row['digest']}"

    @staticmethod
    def receipt_tools():
        try:
            from hermes_cli.theseus_investigation import receipts
        except ModuleNotFoundError:
            import source_receipts as receipts
        return receipts

    def field_scope(self, claim):
        if not claim:
            return None
        row = self.db.execute("SELECT fields FROM memory_field_scopes WHERE claim=?", (claim["id"],)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def check_tools():
        try:
            from hermes_cli.theseus_investigation import checks
        except ModuleNotFoundError:
            import investigation_checks as checks
        return checks

    def record_check_result(self, change, event, quote, citation, *, automatic=False):
        plan, key = number(change["plan"]), token(change["key"])
        name = token(change["name"])
        rows = self.rows("""SELECT p.latest_doc,e.payload FROM memory_check_plans p
                           JOIN memory_events e ON e.id=p.event AND e.project=p.project
                           WHERE p.project=? AND p.event=? AND p.key=?""",plan,key)
        if not rows or plan >= event:
            raise ValueError("choose an existing prospective check")
        candidate = next((op for op in json.loads(rows[0]["payload"]) if op["op"]=="candidate_check"
                          and op["name"]==name and op["key"]==key), None)
        if not candidate:
            raise ValueError("check belongs to a different investigation")
        if any(k not in ("op","name","plan","key") for k in change):
            raise ValueError("the observed class is derived from the source, never supplied by the caller")
        proof = self.checked_exhibit(name,citation,quote,event)
        measurement = candidate["measurement"]
        snapshot = int(self.receipt_tools().CITATION.fullmatch(citation)[2])
        source = self.db.execute("SELECT path,digest FROM docs WHERE id=?", (snapshot,)).fetchone()
        if automatic:
            value, _ = self.receipt_tools().scoped_value(proof["context"],measurement["scope"],measurement["field"])
            if value is None:
                # Keep a withdrawal when the same document replaces an earlier measured result.
                previous = self.db.execute("""SELECT 1 FROM memory_check_results r JOIN docs d ON d.id=r.snapshot
                    WHERE r.project=? AND r.plan=? AND r.key=? AND d.path=? LIMIT 1""",
                    (self.project,plan,key,source[0])).fetchone()
                if not previous:
                    return
            else:
                quote = measurement["field"] + ": " + value
        outcome, reason = self.check_tools().classify(proof,measurement,quote,self.receipt_tools().scoped_value)
        first_seen = self.db.execute("SELECT min(id) FROM docs WHERE digest=?", (source[1],)).fetchone()[0]
        state = self.db.execute("SELECT status FROM source_status WHERE doc=?",(snapshot,)).fetchone()
        result = dict(revision=event, plan=plan, key=key, scope=measurement["scope"],
                      predictions=candidate["predictions"], outcome=outcome, reason=reason,
                      prospective=first_seen>rows[0]["latest_doc"], citation=citation, observation=quote)
        if state and state[0] in ("conflict","superseded"):
            result["status_when_attached"] = state[0]
        existing = self.db.execute("""SELECT data FROM memory_check_results
            WHERE project=? AND plan=? AND key=? AND snapshot=?""",(self.project,plan,key,snapshot)).fetchone()
        if existing:
            # Reattaching the identical archived snapshot never creates another vote.
            if json.loads(existing[0])["outcome"] != outcome:
                raise ValueError("conflicting results within one document require separate measured records")
            return
        self.db.execute("INSERT INTO memory_check_results VALUES(?,?,?,?,?,?)",
                        (self.project,event,plan,key,snapshot,packed(result)))

    def match_plans(self, investigation, event, quote, citation, explicit):
        plans = self.rows("""SELECT p.event,p.key FROM memory_check_plans p
            JOIN memory_events e ON e.id=p.event, json_each(e.payload) j
            WHERE p.project=? AND p.event<? AND json_extract(j.value,'$.op')='candidate_check'
            AND json_extract(j.value,'$.name')=? AND json_extract(j.value,'$.key')=p.key
            GROUP BY p.key HAVING p.event=max(p.event) ORDER BY p.event DESC,p.key LIMIT 8""",
            event,investigation)
        for plan in plans:
            if (investigation,plan["event"],plan["key"]) not in explicit:
                self.record_check_result(dict(op="check_result",name=investigation,plan=plan["event"],
                                              key=plan["key"]),event,quote,citation,automatic=True)

    def checked_exhibit(self, investigation, citation, excerpt, at, *, anchored=False):
        """Resolve an exhibit in this investigation against archived original bytes."""
        receipts = self.receipt_tools()
        entries = self.rows(
            """SELECT json_extract(j.value,'$.kind') AS kind,
                      json_extract(j.value,'$.limitation') AS limitation
               FROM memory_events e,json_each(e.payload) j
               WHERE e.project=? AND e.id<=? AND e.citation=? AND json_extract(j.value,'$.op')='exhibit'
               AND json_extract(j.value,'$.name')=? AND (?=0 OR instr(?,e.evidence)>0)
               ORDER BY e.id DESC LIMIT 1""",
            at, citation, token(investigation), anchored,excerpt,
        )
        if not entries:
            raise ValueError("source is not an exhibit of this investigation")
        checked, _ = receipts.source(self.db, self.project, citation, string(excerpt))
        return checked | entries[0]

    def amend_value(self, change, current, at):
        if not current:
            raise ValueError("no governing text to amend")
        original, replacement = string(change["old"]), string(change["replacement"])
        field, sep, _ = original.partition(": ")
        if (not sep or not field.strip() or original == replacement or
                not replacement.startswith(field + ": ") or
                any("\n" in line or "\r" in line for line in (original,replacement))):
            raise ValueError("select one complete labeled field, retaining its label")
        lines = current["value"].splitlines(keepends=True)
        matches = [i for i,line in enumerate(lines) if line.rstrip("\r\n").startswith(field + ": ")]
        if len(matches) != 1 or lines[matches[0]].rstrip("\r\n") != original:
            raise ValueError("old field is ambiguous or changed")
        item = self.checked_exhibit(change["investigation"], change["citation"], replacement, at)
        subject = string(change["subject"])
        if (item["kind"] != "specimen" or item["coverage"] != "whole section" or
                replacement not in item["context"].splitlines() or subject not in item["context"] or
                subject not in current["value"]):
            raise ValueError("need a complete specimen source field and a shared subject outside the edit")
        self.receipt_tools().scope_field_match(current["value"],item["context"],field,replacement,
                                               subject,self.field_scope(current))
        matched = re.fullmatch(r"record:\d+:snapshot:(\d+):sha256:[a-f0-9]{64}", change["citation"])
        status = self.db.execute("SELECT status FROM source_status WHERE doc=?", (int(matched[1]),)).fetchone()
        if not status or status[0] != "current":
            raise ValueError("source is superseded or in conflict")
        index = matches[0]
        lines[index] = replacement + lines[index][len(original):]
        return "".join(lines)

    def amend_passage(self, change, current, at):
        """Narrow one cited passage to a complete, attributed source paragraph."""
        if not current or self.field_scope(current):
            raise ValueError("choose an existing prose claim; use a scoped field edit for cards")
        original = string(change["old"])
        citation = string(change["citation"])
        short = citation.partition(":sha256:")[0]
        cited = re.findall(r"record:\d+:snapshot:\d+(?::sha256:[a-f0-9]{64})?",original)
        if current["value"].count(original)!=1 or len(cited)!=1 or cited[0] not in (short,citation):
            raise ValueError("select a unique original passage that already cites this record")
        start = current["value"].index(original)
        end = start + len(original)
        if ((start and not re.search(r"(?:\r?\n)[ \t]*(?:\r?\n)$",current["value"][:start])) or
                (end < len(current["value"]) and not re.match(r"(?:\r?\n)[ \t]*(?:\r?\n)",current["value"][end:]))):
            raise ValueError("select the complete cited paragraph including its qualifications")
        excerpt = string(change["excerpt"])
        checked = self.checked_exhibit(change["investigation"],citation,excerpt,at,anchored=True)
        blocks = [p.strip() for p in re.split(r"\n[ \t]*\n",checked["context"].replace("\r\n","\n"))]
        if checked["coverage"]!="whole section" or len(excerpt)>2000 or blocks.count(excerpt)!=1:
            raise ValueError("select one complete, unique source paragraph with its qualifiers")
        if checked["kind"] != "specimen":
            raise ValueError("background alone cannot establish a specimen claim")
        snapshot = int(self.receipt_tools().CITATION.fullmatch(citation)[2])
        state = self.db.execute("SELECT status FROM source_status WHERE doc=?", (snapshot,)).fetchone()
        if not state or state[0] != "current":
            raise ValueError("the cited source is superseded or in conflict")
        paragraph = f'{checked["file"]} ({short}) reports: “{excerpt}”'
        if set(change)-{"op","name","previous","old","excerpt","citation","investigation","reason","edition","job"}:
            raise ValueError("the replacement is generated from the source; free inference requires review")
        return current["value"][:start] + paragraph + current["value"][end:]

    def revision(self, request, quote, citation, changes):
        token(request)
        string(quote)
        if not isinstance(changes, list) or not 1 <= len(changes) <= 64:
            raise ValueError("supply 1 to 64 changes")
        payload = packed(changes)
        prior = self.rows(
            "SELECT * FROM memory_events WHERE project=? AND request=?", request
        )
        if prior:
            if (payload, quote, citation) != (
                prior[0]["payload"],
                prior[0]["evidence"],
                prior[0]["citation"],
            ):
                raise ValueError(
                    "revision key already used for different evidence or changes"
                )
            return prior[0]["id"]
        event = self.db.execute(
            """INSERT INTO memory_events(project,request,payload,evidence,citation)
            VALUES(?,?,?,?,?)""",
            (self.project, request, payload, quote, citation),
        ).lastrowid
        for change in changes:
            kind = change["op"]
            concept = token(change["name"])
            if citation.startswith("record:") and kind not in (
                "observe",
                "exhibit",
                "outcome",
                "check_result",
            ):
                raise ValueError(
                    "source evidence can annotate a thesis; a chat decision is needed to govern work"
                )
            if kind == "investigate":
                if change.get("parent"):
                    parent = token(change["parent"])
                    visited = {concept}
                    while parent:
                        if parent in visited:
                            raise ValueError("cyclic investigation targets")
                        visited.add(parent)
                        parent = self.investigation(parent, at=event - 1)["parent"]
                string(change["question"])
                string(change["scope"])
                string(change["discriminator"])
                if not isinstance(change.get("theses", []), list):
                    raise ValueError("theses must be a list of identifiers")
                for name in change.get("theses", []):
                    token(name)
            elif kind == "candidate_check":
                token(change["key"])
                string(change["procedure"])
                number(change["minutes"])
                predictions = change["predictions"]
                outcomes = change["outcomes"]
                if not isinstance(outcomes, dict) or not 2 <= len(outcomes) <= 6:
                    raise ValueError("declare two to six distinguishable outcomes")
                for key, description in outcomes.items():
                    token(key)
                    string(description)
                hypotheses = {item["name"] for item in self.investigation(concept, at=event-1)["alternatives"]}
                if not isinstance(predictions, dict) or not 2 <= len(predictions) <= 8:
                    raise ValueError("supply predictions for two to eight competing theses")
                if set(predictions)-hypotheses or set(predictions.values())-outcomes.keys():
                    raise ValueError("predictions must use registered theses and named outcomes")
                string(change["inconclusive_if"])
                if "measurement" in change:
                    self.check_tools().validate_measurement(change)
                    row = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone()
                    latest = self.db.execute("SELECT coalesce(max(id),0) FROM docs").fetchone()[0] if row else 0
                    self.db.execute("INSERT INTO memory_check_plans VALUES(?,?,?,?)",
                                    (self.project,event,change["key"],latest))
            elif kind == "check_result":
                self.record_check_result(change,event,quote,citation)
            elif kind == "review":
                if change["move"] not in (
                    "continue",
                    "revise",
                    "redirect",
                    "pause",
                    "stop",
                ):
                    raise ValueError("unknown research move")
                if change.get("method"):
                    token(change["method"])
                string(change["reason"])
                string(change["next_check"])
                seen = self.investigation(concept, at=event - 1)
                if change["basis"] != (seen["current"] or {}).get("id"):
                    raise ValueError("review used an outdated interpretation")
                if change["basis_revision"] != seen["revision"]:
                    raise ValueError(
                        "new project evidence; review the current investigation"
                    )
            elif kind == "outcome":
                if change["result"] not in ("useful", "inconclusive", "failed"):
                    raise ValueError("unknown method outcome")
                if not any(
                    r["revision"] == number(change["review"])
                    for r in self.investigation(concept, at=event - 1)["reviews"]
                ):
                    raise ValueError(
                        "outcome must reference an existing review of this question"
                    )
                string(change["conditions"])
            elif kind == "exhibit":
                if not citation.startswith("record:"):
                    raise ValueError("an exhibit requires an exact source record")
                if change["kind"] not in ("background", "specimen"):
                    raise ValueError("identify the applicability of the source")
                string(change["limitation"])
            elif kind == "observe":
                direction = change["direction"]
                if direction not in ("for", "against", "inconclusive"):
                    raise ValueError("expected for, against or inconclusive")
                if not self.rows(
                    "SELECT 1 FROM memory_theses WHERE project=? AND name=? LIMIT 1",
                    concept,
                ):
                    raise ValueError("unknown thesis")
                self.db.execute(
                    "INSERT INTO memory_observations VALUES(?,?,?,?)",
                    (self.project, concept, event, direction),
                )
            elif kind == "branch":
                parent = token(change["parent"])
                if self.parent(concept, event) or self.rows(
                    "SELECT id FROM memory_claims WHERE project=? AND concept=?",
                    concept,
                ):
                    raise ValueError("a branch needs a new concept name")
                if not self.parent(parent, event) and not self.rows(
                    "SELECT id FROM memory_claims WHERE project=? AND concept=?", parent
                ):
                    raise ValueError("parent concept must already exist")
                ancestor = parent
                while ancestor:
                    if ancestor == concept:
                        raise ValueError("cyclic concept hierarchy")
                    ancestor = self.parent(ancestor, event)
                self.db.execute(
                    "INSERT INTO memory_topics VALUES(?,?,?,?)",
                    (self.project, concept, parent, event),
                )
            elif kind == "thesis":
                status = change.get("status", "open")
                if status not in ("open", "supported", "rejected", "withdrawn"):
                    raise ValueError("invalid thesis status")
                self.db.execute(
                    "INSERT INTO memory_theses VALUES(?,?,?,?,?)",
                    (self.project, concept, event, string(change["value"]), status),
                )
            elif kind in ("decide", "amend", "amend_passage"):
                edition = number(change.get("edition", 1))
                scope = token(change["job"]) if change.get("job") else ""
                current = self.resolve(concept, edition, scope, event)
                if change["previous"] != (current["id"] if current else None):
                    raise ValueError(
                        "decision changed; inspect the governing claim before replacement"
                    )
                if kind in ("amend", "amend_passage"):
                    if not citation.startswith("chat:") or "supports" in change or "value" in change:
                        raise ValueError("amend needs an authorizing chat and preserves the other fields and premises")
                    string(change["reason"])
                    editor = self.amend_value if kind == "amend" else self.amend_passage
                    value = editor(change, current, event - 1)
                    groups = json.loads(current["supports"])
                else:
                    value = string(change["value"])
                    groups = change.get("supports", [])
                if kind == "decide" and "basis_exhibits" in change:
                    string(change["reason"])
                    basis = change["basis_exhibits"]
                    if not isinstance(basis, list) or not 1 <= len(basis) <= 4:
                        raise ValueError("select one to four investigation exhibits")
                    for source in basis:
                        self.checked_exhibit(concept, source["citation"], source["excerpt"], event - 1)
                if not isinstance(groups, list):
                    raise ValueError("supports must be a list of alternative groups")
                for group in groups:
                    if not isinstance(group, list) or not group:
                        raise ValueError(
                            "each independent support group needs at least one premise"
                        )
                    for ref in group:
                        selected = self.governing(
                            token(ref["concept"]), edition, scope, event, (concept,)
                        )
                        if not selected or selected["id"] != number(ref["id"]):
                            raise ValueError("premise is no longer current")
                saved = self.db.execute(
                    """INSERT INTO memory_claims(event,project,concept,scope,edition,value,supports)
                    VALUES(?,?,?,?,?,?,?)""",
                    (
                        event,
                        self.project,
                        concept,
                        scope,
                        edition,
                        value,
                        packed(groups),
                    ),
                )
                field_scope = self.field_scope(current)
                if kind == "decide" and field_scope and value != current["value"] and value not in quote:
                    raise ValueError("scoped card changes require a verified amend or the exact user-supplied replacement")
                if "scope_fields" in change:
                    if kind != "decide" or field_scope is not None:
                        raise ValueError("declare scope fields when creating an unscoped decision")
                    field_scope = change["scope_fields"]
                if field_scope is not None:
                    parsed = self.receipt_tools().fields(value)
                    if (not isinstance(field_scope,list) or not 1 <= len(field_scope) <= 8 or
                            not all(isinstance(k,str) and len(parsed.get(k,[]))==1 for k in field_scope)
                            or len(set(field_scope)) != len(field_scope)):
                        raise ValueError("choose distinct and unambiguous scope fields on the card")
                    self.db.execute("INSERT INTO memory_field_scopes VALUES(?,?)",(saved.lastrowid,packed(field_scope)))
            else:
                raise ValueError("unknown change")
        explicit = {(op["name"],op["plan"],op["key"]) for op in changes if op["op"]=="check_result"}
        for name in {op["name"] for op in changes if op["op"]=="exhibit" and op["kind"]=="specimen"}:
            self.match_plans(name,event,quote,citation,explicit)
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='activations'").fetchone():
            watermark = self.db.execute("SELECT coalesce(max(id),0) FROM activations").fetchone()[0]
            self.db.execute("INSERT INTO memory_archive_heads VALUES(?,?,?)",(self.project,event,watermark))
        jobs = self.rows("SELECT name,done FROM memory_jobs WHERE project=? ORDER BY name")
        self.db.execute("INSERT INTO memory_impact_inventory VALUES(?,?)", (event, packed(jobs)))
        return event

    def investigation(self, concept, edition=1, job="", at=2**63 - 1):
        """Project a cited investigation from the immutable ledger at one revision."""
        token(concept)
        question = None
        evidence = []
        reviews = []
        outcomes = []
        candidates = {}
        for event in self.rows(
            "SELECT * FROM memory_events WHERE project=? AND id<=? ORDER BY id", at
        ):
            for item in json.loads(event["payload"]):
                if item["name"] != concept:
                    continue
                if item["op"] == "investigate":
                    question = item
                if item["op"] == "candidate_check":
                    candidates[item["key"]] = dict(item,revision=event["id"])
                if item["op"] == "outcome":
                    outcomes.append(
                        dict(
                            review=item["review"],
                            result=item["result"],
                            conditions=item["conditions"],
                            evidence=event["evidence"],
                            citation=event["citation"],
                            revision=event["id"],
                        )
                    )
                if item["op"] == "review":
                    reviews.append(
                        dict(
                            method=item.get("method"),
                            move=item["move"],
                            basis=item["basis"],
                            reason=item["reason"],
                            next_check=item["next_check"],
                            revision=event["id"],
                            citation=event["citation"],
                        )
                    )
                if item["op"] == "exhibit":
                    evidence.append(
                        dict(
                            kind=item["kind"],
                            excerpt=event["evidence"],
                            citation=event["citation"],
                            limitation=item["limitation"],
                        )
                    )
        if question is None:
            raise ValueError("no investigation registered at this revision")
        current = self.governing(concept, edition, job, at)
        chain = self.rows(
            """SELECT c.id,c.event,c.value,c.scope,e.citation FROM memory_claims c
            JOIN memory_events e ON e.id=c.event WHERE c.project=? AND c.concept=?
            AND c.edition<=? AND c.scope IN ('',?) AND c.event<=? ORDER BY c.event,c.id""",
            concept,
            edition,
            job,
            at,
        )
        decisions = {r["id"]: json.loads(r["payload"]) for r in self.rows(
            "SELECT id,payload FROM memory_events WHERE project=? AND id IN "
            "(SELECT value FROM json_each(?))", packed([item["event"] for item in chain]))}
        for claim in chain:
            change = next((op for op in decisions[claim["event"]] if op["name"] == concept
                           and op["op"] in ("decide","amend","amend_passage") and op.get("job","") == claim["scope"]
                           and (op.get("value") == claim["value"] or op["op"] in ("amend","amend_passage"))), {})
            if "reason" in change:
                claim["reason"] = change["reason"]
                claim["basis_exhibits"] = change.get("basis_exhibits", [])
                if change["op"] in ("amend","amend_passage"):
                    claim["basis_exhibits"] = [{"citation":change["citation"],
                                              "excerpt":change.get("replacement",change.get("excerpt"))}]
        alternatives = []
        for name in question.get("theses", []):
            rows = self.rows(
                """SELECT name,status,position,event FROM memory_theses
                WHERE project=? AND name=? AND event<=? ORDER BY event DESC LIMIT 1""",
                name,
                at,
            )
            if rows:
                thesis = rows[0]
                thesis["observations"] = self.rows(
                    """SELECT direction,e.evidence,e.citation
                    FROM memory_observations o JOIN memory_events e ON e.id=o.event
                    WHERE o.project=? AND o.thesis=? AND o.event<=? ORDER BY o.event""",
                    name,
                    at,
                )
                alternatives.append(thesis)
        direct = any(item["kind"] == "specimen" for item in evidence)
        results = []
        if self.rows("SELECT 1 FROM memory_check_results WHERE project=? LIMIT 1"):
            measured = self.rows("""SELECT r.data,r.snapshot,d.path,d.digest,s.status FROM memory_check_results r
                JOIN memory_events e ON e.id=r.plan JOIN docs d ON d.id=r.snapshot
                LEFT JOIN source_status s ON s.doc=r.snapshot
                WHERE r.project=? AND r.event<=? AND EXISTS (
                 SELECT 1 FROM json_each(e.payload) j WHERE json_extract(j.value,'$.op')='candidate_check'
                 AND json_extract(j.value,'$.key')=r.key AND json_extract(j.value,'$.name')=?)
                ORDER BY r.event,r.snapshot""",at,concept)
            historical_cursor = None
            if at < 2**63-1:
                historical_cursor = self.db.execute("""SELECT activation FROM memory_archive_heads
                    WHERE project=? AND event<=? ORDER BY event DESC LIMIT 1""",(self.project,at)).fetchone()
            latest = {}
            for row in measured:
                result = json.loads(row["data"])
                origin = (result["plan"],result["key"],row["path"])
                if result.get("status_when_attached") in ("superseded","conflict"):
                    results.append(result)
                    continue
                previous = latest.get(origin)
                if previous is not None:
                    previous["superseded_by"] = result["revision"]
                status = row["status"] if at == 2**63-1 else None
                if historical_cursor:
                    status = self.receipt_tools().snapshot_status(self.db,self.project,row["path"],row["digest"],historical_cursor[0])
                if status in ("superseded","conflict"):
                    result["source_status"] = status
                results.append(result)
                latest[origin] = result
        checks = self.check_tools()
        ranked = checks.ranked_checks(candidates,alternatives,results)
        return dict(
            project=self.project,
            revision=self.db.execute(
                "SELECT max(id) FROM memory_events WHERE project=? AND id<=?",
                (self.project, at),
            ).fetchone()[0],
            question=question["question"],
            scope=question["scope"],
            current=current,
            lineage=chain,
            parent=question.get("parent"),
            outcomes=outcomes,
            reviews=reviews,
            alternatives=alternatives,
            evidence=evidence,
            next_review=(
                "Compare specimen evidence against the competing explanations."
                if direct
                else "Locate a specimen-specific observation before attributing a mechanism."
            ),
            discriminator=question["discriminator"],
            candidate_checks=ranked, check_results=results,
            working_assessments=checks.assess(alternatives,results),
        )

    def method_history(self, method):
        """Across projects, retrieve explicitly recorded outcomes for one named method."""
        return [
            dict(r)
            for r in self.db.execute(
                """
            SELECT a.project,json_extract(r.value,'$.name') AS question,a.id AS review,
                   b.id AS outcome,json_extract(o.value,'$.result') AS result,
                   json_extract(o.value,'$.conditions') AS conditions,b.evidence,b.citation
            FROM memory_events a,json_each(a.payload) r
            JOIN memory_events b ON b.project=a.project
            JOIN json_each(b.payload) o ON json_extract(o.value,'$.review')=a.id
            WHERE json_extract(r.value,'$.op')='review' AND json_extract(r.value,'$.method')=?
            AND json_extract(o.value,'$.op')='outcome'
            AND json_extract(o.value,'$.name')=json_extract(r.value,'$.name')
            ORDER BY b.id DESC LIMIT 25""",
                (token(method),),
            )
        ]

    def investigation_page(self, report, job):
        """Render the same snapshot a model reads, including the actual saved job."""

        def section(title, paragraphs):
            body = "".join("<p>" + escape(str(line)) + "</p>" for line in paragraphs)
            return "<section><h2>" + escape(title) + "</h2>" + body + "</section>"

        blocks = [
            section("Scope", [report["scope"]]),
            section(
                "Current interpretation",
                [(report["current"] or {}).get("value", "Unresolved")],
            ),
            section(
                "Decision history",
                [
                    f"Revision {r['event']} · {r['scope'] or 'project'} · {r['value']} ({r['citation']})"
                    + (" · " + r["reason"] if r.get("reason") else "")
                    for r in report["lineage"]
                ],
            ),
        ]
        for assessment in report["working_assessments"]:
            blocks.append(section("Evidence-based assessment", [assessment["status"],
                "Scope: " + packed(assessment["scope"]), "Compatible explanations: " + ", ".join(assessment["compatible"]),
                *[r["citation"] for r in assessment["results"]]]))
        if report["check_results"]:
            history = []
            for result in report["check_results"][-12:]:
                if "superseded_by" in result:
                    state = f"Superseded at revision {result['superseded_by']}."
                elif result.get("source_status") or result.get("status_when_attached"):
                    state = "Source " + (result.get("source_status") or result["status_when_attached"]) + "."
                elif not result["prospective"]:
                    state = "Source predated the plan."
                else:
                    state = result["reason"]
                history.append(f"Revision {result['revision']} · {result['key']} (plan {result['plan']}) · "
                               + (result["outcome"] or "inconclusive") + ". " + state + "\n"
                               + result["observation"] + "\n" + result["citation"])
            if len(report["check_results"])>12:
                history.insert(0,f"The latest 12 of {len(report['check_results'])} measured records are shown.")
            blocks.append(section("Measured evidence history",history))
        if report["candidate_checks"]:
            check = report["candidate_checks"][0]
            blocks.append(section("Next discriminating check", [
                check["procedure"], "Separates: " + ", ".join(" / ".join(pair) for pair in check["separates"]),
                "Inconclusive if: " + check["inconclusive_if"]]))
        for thesis in report["alternatives"]:
            blocks.append(
                section(
                    thesis["name"],
                    [thesis["status"], thesis["position"]]
                    + [
                        f"{o['direction']} · {o['evidence']}"
                        for o in thesis["observations"]
                    ],
                )
            )
        if report["parent"]:
            blocks.append(section("Parent question", [report["parent"]]))
        for outcome in report["outcomes"]:
            blocks.append(
                section(
                    "Method outcome · " + outcome["result"],
                    [outcome["conditions"], outcome["evidence"], outcome["citation"]],
                )
            )
        for review in report["reviews"]:
            blocks.append(
                section(
                    "Research decision · " + review["move"],
                    [review["reason"], review["next_check"], review["citation"]],
                )
            )
        for entry in report["evidence"]:
            provenance = []
            record = re.fullmatch(
                r"record:(\d+):snapshot:(\d+):sha256:([a-f0-9]{64})", entry["citation"]
            )
            if record:
                snapshot = self.db.execute(
                    "SELECT path,substr(data,1,4096) AS header FROM docs WHERE id=? AND digest=?",
                    (record[2], record[3]),
                ).fetchone()
                if snapshot:
                    header = bytes(snapshot["header"]).decode("utf-8", errors="replace")
                    notebook = re.search(
                        r"^Notebook: (https://notebook\.google\.com/notebook/[0-9a-f-]{36})$",
                        header,
                        re.MULTILINE,
                    )
                    if notebook:
                        provenance.extend([header.splitlines()[0], notebook[1]])
                    provenance.append(snapshot["path"])
            blocks.append(
                section(
                    "Evidence · " + entry["kind"],
                    [
                        entry["excerpt"],
                        entry["limitation"],
                        entry["citation"],
                        *provenance,
                    ],
                )
            )
        blocks.append(
            section("Next review", [report["next_review"], report["discriminator"]])
        )
        if job:
            rows = self.rows(
                "SELECT * FROM memory_jobs WHERE project=? AND name=?", token(job)
            )
            if rows:
                row = rows[0]
                blocks.append(
                    section("Paused draft", [json.loads(row["draft"])["body"]])
                )
                if row["done"]:
                    actual = (
                        self.root / "results" / self.project / (job + ".md")
                    ).read_text()
                    if actual != json.loads(row["prepared"])["body"]:
                        raise ValueError("saved file differs from the published job")
                    blocks.append(section("Verified saved file", [actual]))
        title = escape(report["question"])
        return (
            "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>"
            + title
            + "</title><style>body{max-width:54rem;margin:3rem auto;padding:0 1rem;font:17px/1.6 system-ui;color:#182c30}"
            "h1,h2{line-height:1.2}section{border-top:1px solid #b5c2c2;margin-top:2rem}p{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
            + "<main><h1>"
            + title
            + "</h1>"
            + "".join(blocks)
            + "</main></html>"
        )

    def refresh_journal(self, home):
        """Publish a complete profile snapshot while holding the ledger writer lock."""
        sections = []
        recent = self.db.execute("""SELECT e.project, MAX(e.id) revision FROM memory_impact_inventory i
            JOIN memory_events e ON e.id=i.event WHERE i.jobs!='[]' GROUP BY e.project
            ORDER BY revision DESC LIMIT 3""").fetchall()
        for project, revision in recent:
            report = Memory.from_connection(home, project, self.db).impact(revision)
            if not report["jobs"]:
                continue
            items = []
            for job in report["jobs"]:
                items.append("<h3>" + escape(job["job"]) + " · " + job["state_at_revision"] + "</h3>")
                for line in job["lines"]:
                    before = line["before"].get("body",line["before"].get("pending",""))
                    after = line["after"].get("body",line["after"].get("pending",""))
                    items.append(f'<p>Line {line["line"]}<br><del>{escape(before)}</del><br>{escape(after)}</p>')
            sections.append("<details open><summary>" + escape(project) + f" · correction {revision}</summary>"
                            + "<p>" + escape(report["revision"]["evidence"]) + "</p>" + "".join(items) + "</details>")
        rows = self.db.execute("""SELECT DISTINCT project,json_extract(j.value,'$.name') AS concept
            FROM memory_events,json_each(payload) j WHERE json_extract(j.value,'$.op')='investigate'
            ORDER BY project,concept""").fetchall()
        for project, concept in rows:
            memory = Memory.from_connection(home, project, self.db)
            jobs = memory.rows(
                "SELECT name,edition,template FROM memory_jobs WHERE project=? ORDER BY name"
            )
            scopes = [("", 1)] + [
                (j["name"], j["edition"])
                for j in jobs
                if any(
                    concept in match.groups() for match in SLOT.finditer(j["template"])
                )
            ]
            for job, edition in scopes:
                report = memory.investigation(concept, edition, job)
                try:
                    body = (
                        memory.investigation_page(report, job)
                        .split("<main>", 1)[1]
                        .split("</main>", 1)[0]
                    )
                except (OSError, ValueError) as exc:
                    body = (
                        "<p>Artifact verification failed: " + escape(str(exc)) + "</p>"
                    )
                label = project.upper() + " · " + (job or "Project interpretation")
                sections.append(
                    "<details open><summary>"
                    + escape(label)
                    + "</summary>"
                    + body
                    + "</details>"
                )
        page = (
            "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Investigations · Theseus</title><style>body{max-width:56rem;margin:2rem auto;padding:0 1rem;font:16px/1.55 system-ui;color:#183438}"
            "details{border-top:2px solid #496c70;padding:1rem 0}summary{cursor:pointer;font-weight:bold}"
            "section{border-top:1px solid #ccd6d4}h1,h2{line-height:1.2}p{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
            "<h1>Investigations</h1>"
            + ("".join(sections) or "<p>No investigations yet.</p>")
            + "</html>"
        )
        with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as out:
            stage = Path(out.name)
            try:
                out.write(page.encode())
                out.flush()
                os.fsync(out.fileno())
                os.replace(stage, self.root / "investigations.html")
            finally:
                stage.unlink(missing_ok=True)
        return str(self.root / "investigations.html")

    def assessment_text(self, concept, edition, job, at=2**63 - 1):
        report = self.investigation(concept,edition,job,at)
        theses = {a["name"]:a["position"] for a in report["alternatives"]}
        items, provenance = [], []
        for group in report["working_assessments"]:
            scope = ", ".join(k+"="+v for k,v in sorted(group["scope"].items()))
            if "leader" in group:
                statement = theses[group["leader"]] + " (provisional among the tested alternatives)"
            else:
                statement = group["status"] + "."
            references = list(dict.fromkeys(row["citation"] for row in group["results"]))
            items.append(f"{scope} — {statement} " + " ".join(r.partition(":sha256:")[0] for r in references))
            provenance.extend(references)
        if not items:
            items.append("Unresolved. No applicable current measurement distinguishes the registered alternatives.")
        return "\n".join(items), list(dict.fromkeys(provenance))

    def render(self, template, edition, job, at=2**63 - 1):
        refs = {}
        assessments = {}

        captured = None
        if "@pause}}" in template:
            rows = self.rows("SELECT draft FROM memory_jobs WHERE project=? AND name=?", job)
            if rows:
                captured = json.loads(rows[0]["draft"])["claims"]

        def fill(match):
            label = match[0][2:-2]
            if match[3]=="@assessment":
                if match[2]:
                    raise ValueError("use the investigation name for an assessment slot")
                text, citations = self.assessment_text(match[1],edition,job,at)
                assessments[label] = citations
                return text
            if match[3]=="@pause" and captured is not None:
                original = self.rows(
                    "SELECT id,value FROM memory_claims WHERE project=? AND id=?",
                    captured.get(label),
                )
                if not original:
                    raise ValueError(f"missing pause-time claim for {label}")
                refs[label] = original[0]["id"]
                return original[0]["value"]
            name = match[1]
            branch = match[2]
            if branch:
                parent = self.parent(branch, at)
                if parent and parent != name:
                    raise ValueError(f"{branch} belongs to another concept")
                if parent:
                    name = branch
            selected = (
                self.resolve(name, edition, job, at)
                if branch and not parent
                else self.governing(name, edition, job, at)
            )
            if not selected:
                raise ValueError(
                    f"no currently supported decision for {name}; job remains paused"
                )
            refs[label] = selected["id"]
            return selected["value"]

        body = SLOT.sub(fill, string(template))
        if not (refs or assessments) or "{{" in body or "}}" in body:
            raise ValueError("template needs well-formed {{concept}} slots")
        result = {"body":body, "claims":refs}
        if assessments:
            result["assessments"] = assessments
        return result

    def impact(self, event):
        """Compare two historical resolutions. A later edit or publication cannot alter this report."""
        events = self.rows("SELECT id,evidence,citation,payload FROM memory_events WHERE project=? AND id=?", number(event))
        if not events:
            raise ValueError("revision is not in this project")
        correction = events[0]
        edits = json.loads(correction.pop("payload"))
        table = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_impact_inventory'").fetchone()
        inventory = self.db.execute("SELECT jobs FROM memory_impact_inventory WHERE event=?", (event,)).fetchone() if table else None
        changes = []
        # A scoped claim can inherit a general premise. Include both authored scopes and
        # concrete job scopes; use the ordinary resolver so alternatives remain independent.
        records = self.rows("SELECT DISTINCT concept,scope,edition FROM memory_claims WHERE project=? AND event<=? ORDER BY concept,scope,edition", event)
        jobs = []
        if inventory:
            for item in json.loads(inventory[0]):
                row, = self.rows("SELECT name,edition,template,prepared FROM memory_jobs WHERE project=? AND name=?", item["name"])
                jobs.append((row, bool(item["done"])))
        scopes = {(r["scope"],r["edition"]) for r in records} | {(r["name"],r["edition"]) for r,_ in jobs}
        for concept in sorted({r["concept"] for r in records}):
            for scope, edition in sorted(scopes):
                pair = [self.governing(concept, edition, scope, at) for at in (event-1,event)]
                ids = [c["id"] if c else None for c in pair]
                if ids[0] == ids[1]:
                    continue
                values = []
                for claim in pair:
                    if not claim:
                        values.append(None)
                        continue
                    origin, = self.rows("SELECT citation FROM memory_events WHERE project=? AND id=?", claim["event"])
                    values.append(dict(id=claim["id"], revision=claim["event"], citation=origin["citation"],
                                       value=claim["value"], supports=json.loads(claim["supports"])))
                changes.append(dict(concept=concept, job=scope or None, edition=edition,
                                    before=values[0], after=values[1]))
        affected = []
        for row, published in jobs:
            lines = []
            for index, line in enumerate(row["template"].splitlines(),1):
                if not SLOT.search(line):
                    continue
                sides = []
                for at in (event-1,event):
                    try:
                        sides.append(self.render(line,row["edition"],row["name"],at))
                    except ValueError as exc:
                        sides.append({"pending": str(exc)})
                if sides[0] != sides[1]:
                    lines.append(dict(line=index, template=line, before=sides[0], after=sides[1]))
            if lines:
                item = dict(job=row["name"], state_at_revision="published" if published else "paused", lines=lines)
                if published and row["prepared"]:
                    item["published_sha256"] = hashlib.sha256(json.loads(row["prepared"])["body"].encode()).hexdigest()
                affected.append(item)
        return dict(project=self.project, revision=correction, changes=edits, claims=changes,
                    jobs=affected, jobs_at_revision=len(jobs) if inventory else None,
                    coverage="Before and after are rendered at this revision. Published files remain immutable. Coverage is limited to registered claims and template slots."
                    if inventory else "Historical claims only. Job membership was not recorded for this older revision.")

    def pause(self, name, edition, template):
        token(name)
        number(edition)
        rows = self.rows("SELECT * FROM memory_jobs WHERE project=? AND name=?", name)
        if rows:
            if (edition, template) != (rows[0]["edition"], rows[0]["template"]):
                raise ValueError("job name already in use")
            return json.loads(rows[0]["draft"])
        draft = self.render(template, edition, name)
        self.db.execute(
            "INSERT INTO memory_jobs(project,name,edition,template,draft) VALUES(?,?,?,?,?)",
            (self.project, name, edition, template, packed(draft)),
        )
        return draft

    def resume(self, name):
        token(name)
        while True:
            result = self._publish(name)
            if result is not None:
                return result

    def _publish(self, name):
        folder = self.root / "results" / self.project
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = folder / (name + ".md")
        (row,) = self.rows("SELECT * FROM memory_jobs WHERE project=? AND name=?", name)
        prior = json.loads(row["prepared"]) if row["prepared"] else None
        if path.is_symlink():
            raise ValueError("unexpected output link")
        if path.exists() or row["done"]:
            if (
                not prior
                or not path.is_file()
                or path.read_bytes() != prior["body"].encode()
            ):
                raise ValueError(
                    "existing result missing or changed; inspect before retry"
                )
            actual = prior
        else:
            actual = self.render(row["template"], row["edition"], name)
            if shadow is not None:
                shadow.propose(self.root.parent, "resume", self.project + "/" + name,
                               packed([actual["claims"],actual["assessments"]]) if "assessments" in actual else packed(actual["claims"]), lambda: {
                    "draft_claims": json.loads(row["draft"])["claims"], "current_claims": actual["claims"],
                    "expected_sha256": shadow.digest(actual["body"]),
                    "draft_sha256": shadow.digest(json.loads(row["draft"])["body"])})
            if actual != prior:
                # Commit intent before file publication. Reacquire the writer lock and
                # resolve again: any intervening correction must win before first save.
                self.db.execute(
                    "UPDATE memory_jobs SET prepared=? WHERE project=? AND name=?",
                    (packed(actual), self.project, name),
                )
                self.db.commit()
                self.db.execute("BEGIN IMMEDIATE")
                return None
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as tmp:
                stage = Path(tmp.name)
                try:
                    tmp.write(actual["body"].encode())
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    os.link(stage, path)
                finally:
                    stage.unlink(missing_ok=True)
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        reopened = path.read_bytes()
        if reopened != actual["body"].encode():
            raise ValueError("published bytes differ from the governing rendering; inspect before completing")
        sha256 = hashlib.sha256(reopened).hexdigest()
        self.db.execute(
            "UPDATE memory_jobs SET done=1 WHERE project=? AND name=?",
            (self.project, name),
        )
        if shadow is not None:
            shadow.outcome(self.root.parent, "resume", self.project + "/" + name,
                           packed([actual["claims"],actual["assessments"]]) if "assessments" in actual else packed(actual["claims"]), lambda: {
                "path": str(path), "sha256": sha256,
                "matches_governing_render": True})
        return {
            "path": str(path),
            "draft": json.loads(row["draft"]),
            "saved": actual,
            "sha256": sha256,
            "readback": {"matches_governing_render": True, "bytes": len(reopened)},
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    cmds = parser.add_subparsers(dest="action", required=True)
    revise = cmds.add_parser("revise")
    revise.add_argument("key")
    revise.add_argument("quote")
    revise.add_argument("changes", type=json.loads)
    revise.add_argument(
        "--record", type=int, help="cite project-recall evidence for an observation"
    )
    lookup = cmds.add_parser("lookup")
    lookup.add_argument("concept")
    lookup.add_argument("edition", type=int)
    lookup.add_argument("--job", default="")
    lookup.add_argument(
        "--base",
        action="store_true",
        help="inspect the inherited default when editing a split topic",
    )
    lookup.add_argument(
        "--at", type=int, default=2**63 - 1, help="known as of revision"
    )
    cmds.add_parser("inspect").add_argument("job")
    cmds.add_parser("impact", help="show what a specific correction changed").add_argument("revision", type=int)
    investigation = cmds.add_parser("investigation")
    investigation.add_argument("concept")
    investigation.add_argument(
        "--html", action="store_true", help="print a current HTML investigation view"
    )
    investigation.add_argument("--edition", type=int, default=1)
    investigation.add_argument("--job", default="")
    investigation.add_argument("--at", type=int, default=2**63 - 1)
    cmds.add_parser("methods").add_argument("method")
    cmds.add_parser("journal")
    cmds.add_parser("history")
    cmds.add_parser("theses").add_argument("--at", type=int, default=2**63 - 1)
    pause = cmds.add_parser("pause")
    pause.add_argument("job")
    pause.add_argument("edition", type=int)
    pause.add_argument("template")
    cmds.add_parser("resume").add_argument("job")
    args = parser.parse_args()
    os.umask(0o077)
    home = Path(os.environ["HERMES_HOME"])
    mem = Memory(home, args.project)
    with mem.db:
        mem.db.execute(
            "BEGIN IMMEDIATE"
            if args.action in ("revise", "pause", "resume", "journal")
            else "BEGIN"
        )
        if args.action == "revise":
            result = mem.revision(
                args.key,
                args.quote,
                mem.cite(args.record, args.quote)
                if args.record is not None
                else chat_source(home, args.quote),
                args.changes,
            )
        elif args.action == "lookup":
            reader = mem.resolve if args.base else mem.governing
            result = reader(args.concept, args.edition, args.job, args.at)
        elif args.action == "investigation":
            result = mem.investigation(args.concept, args.edition, args.job, args.at)
            if args.html:
                if args.at != 2**63 - 1:
                    raise ValueError(
                        "HTML includes live job state; use JSON for a historical view"
                    )
                print(mem.investigation_page(result, args.job))
                return
        elif args.action == "methods":
            result = mem.method_history(args.method)
        elif args.action == "journal":
            result = mem.refresh_journal(home)
        elif args.action == "history":
            result = mem.rows("SELECT * FROM memory_events WHERE project=? ORDER BY id")
        elif args.action == "theses":
            result = mem.rows(
                """SELECT t.name,t.position,t.status,t.event,e.citation,e.evidence
                FROM memory_theses t JOIN memory_events e ON e.id=t.event WHERE t.project=?
                AND t.event=(SELECT max(n.event) FROM memory_theses n WHERE n.project=t.project AND n.name=t.name AND n.event<=?)
                ORDER BY t.name""",
                args.at,
            )
            for thesis in result:
                thesis["observations"] = mem.rows(
                    """SELECT o.direction,e.evidence,e.citation,e.id AS revision
                    FROM memory_observations o JOIN memory_events e ON e.id=o.event
                    WHERE o.project=? AND o.thesis=? AND o.event<=? ORDER BY o.event""",
                    thesis["name"],
                    args.at,
                )
        elif args.action == "impact":
            result = mem.impact(args.revision)
        elif args.action == "inspect":
            (row,) = mem.rows(
                "SELECT * FROM memory_jobs WHERE project=? AND name=?", token(args.job)
            )
            result = {
                "job": row["name"],
                "edition": row["edition"],
                "done": bool(row["done"]),
                "original_draft": json.loads(row["draft"]),
            }
            try:
                result["current_preview"] = mem.render(
                    row["template"], row["edition"], row["name"]
                )
            except ValueError as exc:
                result["blocked"] = str(exc)
            if row["done"]:
                result["published"] = json.loads(row["prepared"])
        elif args.action == "pause":
            result = mem.pause(args.job, args.edition, args.template)
        else:
            result = mem.resume(args.job)
        if args.action in ("revise", "pause", "resume"):
            mem.db.commit()
            mem.db.execute("BEGIN IMMEDIATE")
            mem.refresh_journal(home)
        print(packed(result))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
        raise SystemExit(str(error)) from None
