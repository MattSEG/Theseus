"""Theseus profile plugin: rehydrate current decisions before work and detect mid-turn revisions."""

import hashlib
import http.client
import importlib.util
import json
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from hermes_cli import theseus_shadow as shadow
from hermes_cli import theseus_investigation as sources
from hermes_cli.theseus_research import routing as research_routing, rule_trials
from hermes_constants import get_hermes_home

ROUTING_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_route_choices(session TEXT PRIMARY KEY, turn TEXT NOT NULL, choices TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS investigation_choices(session TEXT PRIMARY KEY, question TEXT NOT NULL, projects TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_chat_projects(session TEXT PRIMARY KEY, project TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS planning_context_receipts(session TEXT,turn TEXT,project TEXT,receipt TEXT,
 PRIMARY KEY(session,turn));
CREATE TABLE IF NOT EXISTS memory_context_receipts(session TEXT,turn TEXT,request_digest TEXT,context_digest TEXT,
 PRIMARY KEY(session,turn));
CREATE TABLE IF NOT EXISTS memory_turn_reads(session TEXT,turn TEXT,project TEXT,revision INTEGER,
    blocked_request TEXT,PRIMARY KEY(session,turn));
"""
GUIDANCE = """Use current project decisions when answering ordinary questions, drafting, editing or resuming work.
When the user sets or corrects a durable project decision or interpretation, use the revisable-memory skill to record their exact words under the project's stable name in this turn. Do this even for a simple acknowledgement. Use one concept for successive versions of the same interpretation. Preserve chronology, effective scope and uncertainty. A better-supported explanation can govern the current interpretation while an earlier hypothesis remains historical. Distinguish proposals from decisions. Only bring back superseded explanations when discussing history, alternatives or evidence. A draft, old chat, imported file or model inference cannot overrule a user's correction. For questions about a named file, read it first. Stop when it supplies the facts and their limits. Missing causal proof can be answered by asking for the discriminating measurement. Consult investigation records for a material project-specific gap, a comparison requested by the user, or a current decision. Never store authentication or secrets."""
RETRY_GUIDANCE = (
    "On a retry, compare the authorized work with the actual saved destination in one read-only operation "
    "when possible. Open the named inputs and destination directly. When no names were supplied, list filenames "
    "to select the relevant records. Never use a directory-wide content read to combine checks. Open a referenced "
    "exception only after the primary comparison identifies it. Treat the folder or project the user specified as "
    "the boundary for this job. Search names or an index inside that boundary. Consult an external record only "
    "if the user or this job explicitly links its location and the record applies to this job. A matching ID in "
    "a sibling job cannot authorize this one. When the cited record is absent, state where you checked and leave "
    "the discrepancy pending. Do not expand the search to parent folders or unrelated documents. Compare stable identities, required values and "
    "duplicate counts. If every item matches "
    "and no correction or exception is outstanding, report completion immediately and leave the destination "
    "untouched. This comparison is the verification. A missing confirmation alone calls for no history "
    "search, skill lookup or second verification. If a value conflicts or a governing correction is "
    "referenced, consult the specific source that can resolve it before writing. Leave unresolved items pending."
)
INVESTIGATION_REASONING = """When choosing a next check, start with the simplest mechanism supported by an actual observation. Explain which observation favors it and what it leaves open. Trace how the observation was produced: could changing the way the system is accessed or measured produce the same reading as a change in the system itself? For each plausible location of the simplest cause, ask whether the suspected component is necessary there. If the same component is present at the measurement interface and within the subject, design matched cases changing it only at the interface, only within the observed region, and nowhere. Specify the actual intervention in each place and a readout that localizes the effect. Holding an interface fixed or adding a second readout alone cannot test necessity in the other location. Give the contrasting outcomes that would distinguish the explanations. Keep a more elaborate mechanism in reserve until the simpler one fails a fair test."""
MEASUREMENT_CHECK = (
    "Keep the reported endpoint in its original terms. When plausible causes predict the same observation, "
    "leave attribution open and ask for the smallest feasible controlled contrast, what to hold fixed, and "
    "the differing results each explanation predicts. Check that each predicted result compares both sides: a reference on one side alone can establish its present state, but leaves the earlier state unknown. Even a reproduced contrast applies to the tested conditions; attributing the original event needs a link to that event."
)
CAUSAL_CHECK = (
    MEASUREMENT_CHECK + " "
    "When designing or interpreting a causal test, audit the proposed comparison before answering. "
    "List what changes between the cases, including handling, fixture, and measurement method. "
    "Keep those matched. If a second change is unavoidable, cross it independently or state "
    "which joint effect is all the test can identify. Start with the simplest explanation "
    "supported by the observations. For a positive result, distinguish locating a contributor "
    "from identifying its physical mechanism. Name a plausible rival that predicts the same "
    "result and an independent observation or intervention with different predictions. "
    "Check every conclusion, including qualifications, table rows and the final summary: "
    "what else could produce that observation? State what the result establishes, which "
    "explanations it favors, and which relevant alternative remains. Apply this to negative, "
    "mixed and failed-control outcomes too. If a fault disappears, report the exposure and "
    "detection limits. Intermittency, drift or an unexercised condition can yield a clean run. "
    "Attribute a repair only when the comparison distinguishes it from those alternatives. "
    "For discordant observations in different cases, report each separately. Distinct "
    "locations or routes do not determine the number of independent causes. Check whether "
    "a shared condition, persistent alternative, or observation error also fits. Before using "
    "a causal or confidence word, try to falsify the attribution itself: assume the named cause "
    "is absent and construct a concrete way the very same result could recur. Include a rival "
    "that would survive the proposed reset, repeat, or swap. Compare the predictions of the "
    "candidate and that rival for the actual test. If both predict the observation, report "
    "their shared prediction and the unresolved alternatives; reserve attribution for a contrast "
    "on which their predictions differ. Apply this check inside proposed follow-ups too, "
    "before interpreting either outcome. State what remains bundled with a part that moves. "
    "For a successful adjustment, separate its effect on the endpoint from the origin of the "
    "problem. Ask what other change it could be compensating for if the original setting "
    "was appropriate. Give a concrete compensating path and an independent observation with "
    "different predictions for that path and the proposed cause. Repeating or reversing the "
    "adjustment can verify leverage over the outcome while leaving the origin unresolved. "
    "Apply this distinction to the repair decision and to every follow-up interpretation. "
    "Treat containment as a separate operational claim requiring evidence of an effective control. "
    "Before interpreting repeated trials, reconstruct assignment by position in the sequence. "
    "Could practice, fatigue, drift, a periodic reset, or exposure to a previous condition "
    "predict the observed pattern? Identify what is confounded; break the link using random "
    "assignment or balanced order. When carryover cannot be undone, compare independent "
    "first exposures. Distinguish independent units from repeat readings. "
    "Before naming a cause in the report, identify the completed discriminating test and "
    "its observed result. If that result is pending, missing or hypothetical, keep the cause "
    "open, record the available observation, and express predictions only conditionally. "
    "Do not promote a planned check or its predicted result into an incident finding. "
    "With a hard time budget choose the feasible first comparison that best separates the "
    "leading explanations, and label any further checks as future work. For a concise handoff, give the observed "
    "contrast and its material limit, the feasible check, and the result that would weaken the leading explanation. "
    "For a brief handoff, fit those points into three short bullets when possible. Include any material confound "
    "once, beside the conclusion it limits. Apply the remaining audit questions internally."
)
MAX_CONCEPTS = 60


@lru_cache(maxsize=4)
def runtime(home):
    path = home / "skills/research/revisable-memory/scripts/memory.py"
    spec = importlib.util.spec_from_file_location("theseus_profile_memory", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def connect(home):
    root = home / "project-recall"
    root.mkdir(mode=0o700, exist_ok=True, parents=True)
    db = sqlite3.connect(root / "records.sqlite", timeout=10)
    db.executescript(runtime(home).SCHEMA + ROUTING_SCHEMA)
    db.row_factory = sqlite3.Row
    return db


def selected_project(db, session, message):
    names = [r[0] for r in db.execute("SELECT DISTINCT project FROM memory_events")]

    def mentioned(name):
        phrase = r"[\s_-]+".join(map(re.escape, re.split("[-_]", name)))
        return re.search(r"(?<!\w)" + phrase + r"(?!\w)", message, re.IGNORECASE)

    matches = {n for n in names if mentioned(n)}
    # Respect a named switch even in a sentence that mentions the old project.
    named = re.search(
        r'\bproject\s+([`"]?)([a-z][a-z0-9_-]{1,79})\b', message, re.IGNORECASE
    )
    if named and (
        named[1] or named[2].isupper() or "-" in named[2] or named[2].lower() in names
    ):
        return named[2].lower()
    if len(matches) == 1:
        return matches.pop()
    if matches:
        return ""
    # A first revision can have been made in this session since the last turn.
    row = db.execute(
        """SELECT project FROM memory_events WHERE substr(citation,1,length(?))=?
        ORDER BY id DESC LIMIT 1""",
        (f"chat:{session}:message:",) * 2,
    ).fetchone()
    bound = db.execute(
        "SELECT project FROM memory_chat_projects WHERE session=?", (session,)
    ).fetchone()
    if bound or row:
        return bound[0] if bound else row[0]
    return None


def investigation_matches(db, message):
    # Fresh chats may refer to a question by its distinctive subject phrase.
    # Avoid generic overlap ("what explains the", "do we think").
    stop = set(
        "what which where when how why can would could should do did does we our the this those these a an of for is are was were in on to and".split()
    )

    def subjects(text):
        words = re.findall(r"[^\W_]+", text.casefold())
        return {
            tuple(words[i : i + 3])
            for i in range(len(words) - 2)
            if not stop.intersection(words[i : i + 3])
        }

    cues = subjects(message)
    if not message.strip():
        return []
    questions = db.execute("""SELECT project, json_extract(j.value,'$.name'),json_extract(j.value,'$.question'),max(e.id)
        FROM memory_events e, json_each(e.payload) j WHERE json_extract(j.value,'$.op')='investigate'
        GROUP BY project,json_extract(j.value,'$.name')""")
    return [
        (project, concept)
        for project, concept, question, _ in questions
        if cues & subjects(question)
        or question.strip().casefold() == message.strip().casefold()
    ]


def project_investigations(db, project, text, matches):
    """Route an explicitly named project without guessing across its investigations."""
    exact = [concept for name,concept in matches if name==project]
    if exact:
        return exact
    names = db.execute("""SELECT DISTINCT json_extract(j.value,'$.name') FROM memory_events e,json_each(e.payload) j
        WHERE e.project=? AND json_extract(j.value,'$.op')='investigate' ORDER BY 1""",(project,)).fetchall()
    def mentions(name):
        pattern = r"[\s_-]+".join(map(re.escape,re.split("[-_]",name)))
        return re.search(r"(?<!\w)"+pattern+r"(?!\w)",text,re.I)
    named = [row[0] for row in names if mentions(row[0])]
    if named:
        return named
    if len(names)==1 and mentions(project):
        return [names[0][0]]
    return []


def head(db, project):
    return db.execute(
        "SELECT coalesce(max(id),0) FROM memory_events WHERE project=?", (project,)
    ).fetchone()[0]


def semantic_scores(query, excerpts):
    """Batch normalized MiniLM vectors on loopback; ordinary lexical recall stays available."""
    conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=2)
    try:
        body = json.dumps({"model": "all-minilm", "input": [query[:1200], *excerpts]})
        conn.request("POST", "/api/embed", body, {"Content-Type": "application/json"})
        reply = conn.getresponse()
        if reply.status != 200:
            return None
        vectors = json.loads(reply.read(512_000))["embeddings"]
        if len(vectors) != len(excerpts) + 1 or any(len(v) != 384 for v in vectors):
            return None
        return [sum(a * b for a, b in zip(vectors[0], v)) for v in vectors[1:]]
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
        return None
    finally:
        conn.close()


# A conservative screen for explicit condition phrases. The answering model checks
# paraphrases, polarity, the measured endpoint and the full source limitations.
def condition_supported(item, condition):
    def terms(text):
        ignored = {"the", "a", "an", "with", "in", "of", "to", "only",
                   "was", "were", "is", "are", "being", "during", "under", "at"}
        return set(re.findall(r"[-+]?\d+(?:[.]\d+)?|[^\W_]+", text.casefold().replace("−", "-"))) - ignored

    needed = terms(condition)
    clauses = re.split(r"(?<!\d)\.|\.(?!\d)|[;!?]|\bbut\b", item["excerpt"] + ". " + item["limitation"],
                       flags=re.IGNORECASE)
    for clause in clauses:
        if re.search(r"\b(no|never|not|untested|unverified|excluding|except)\b", clause, re.I):
            continue
        if re.search(r"\bwithout\b", clause, re.I) and "without" not in needed:
            continue
        if needed and needed <= terms(clause):
            return True
    return False


def explicitly_excluded(item, query):
    # Use only distinctive words from a source's negative scope statement.
    # A word also present in its affirmative observation cannot alone identify
    # the excluded subject (e.g. both the mock-up and installation are counters).
    def cues(text):
        return set(re.findall(r"[^\W_]{3,}", text.casefold()))

    generic = cues("the and that this were was have has did any how many which "
                   "what test tested testing result recorded data from with no not")
    requested = cues(query.partition("?")[0]) - generic - cues(item["excerpt"])
    for clause in re.split(r"[.;!?]", item["limitation"]):
        if re.search(r"\b(no|never|not|untested|excluding|except)\b", clause, re.I):
            if requested & cues(clause):
                return True
    return False


def requested_conditions(query):
    # Keep the original condition (including 'off', quantities, and qualifiers).
    # Additional action questions do not describe the requested measurement.
    measurement = query.partition("?")[0]
    return [m[0].strip() for m in re.finditer(
        r"\b(?:under|during|with|without|at)\s+([^,;:?!]+?)(?=,|;|:|[?!]|$)",
        measurement, re.I)]


SCOPE_GUIDANCE = (
    "Match subject, condition and endpoint in a single observation, checking limitations, "
    "negations and equivalent wording. Cite the measured result and its scope. Report the "
    "files and record IDs checked, any partial or unverifiable coverage and omitted exhibits. "
    "Lines identify sections. The receipt already matched stored citations and text: cite its "
    "record/snapshot IDs. Do not load recall or citation skills or run formatting checks for "
    "completed work. Use a tool only for evidence that could change the answer. When an "
    "identified accessible record outside the batch could settle a gap, read it directly "
    "once and cite it separately (read_file for a local file). A pointer is not a result. "
    "If evidence is insufficient, say 'No matching result was recorded in the records I "
    "checked.' Give no estimate. Keep that scope throughout; identify the missing record "
    "without assuming a test never happened. Apply the current decision separately, "
    "preserving its terms. Provide diagnoses or experimental design only when asked."
)


def older_exhibit(evidence, query, question):
    """Select one intact, source-linked supplement from before the recent window."""
    def words(text):
        return set(re.findall(r"[^\W_]+", text.casefold()))

    stop = words("what which where when who how why the and for with from was were "
                 "can would could should does did have has this that about please "
                 "show explain tell give summarize report current project evidence")
    focus = {w for w in words(query) - words(question) - stop if len(w) > 2}
    if not focus:
        return None
    candidates = [item for item in reversed(evidence[-36:-4])
                  if len(json.dumps(item, ensure_ascii=False).encode("utf-8")) <= 1200]
    if not candidates:
        return None
    literal = [len(focus & words(item["excerpt"])) for item in candidates]
    subject = question.strip().rstrip("?").casefold()
    description = query.casefold().removeprefix(subject).strip(" ?:-—")
    scores = semantic_scores(description, [item["excerpt"] for item in candidates])
    if scores:
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        first = ranked[0]
        if scores[first] >= 0.28 and (len(ranked) == 1 or scores[first] - scores[ranked[1]] >= 0.06):
            return candidates[first]
    best = max(range(len(literal)), key=literal.__getitem__)
    return candidates[best] if literal[best] else None


def lookup_exhibits(db, project, supplements):
    """Check the cited excerpts from all routed briefs in one snapshot query."""
    entries = [item for _, evidence in supplements for item in evidence]
    if not entries:
        return {}
    requests = []
    for item in entries:
        match = re.fullmatch(r"record:(\d{1,18}):snapshot:(\d{1,18}):sha256:([a-f0-9]{64})", item["citation"])
        if match:
            requests.append([item["citation"], int(match[1]), int(match[2]), match[3], item["excerpt"]])
    if not requests:
        return {}
    sql = """
        SELECT d.path,d.digest,d.data,st.status,json_group_array(json_array(
               json_extract(r.value,'$[0]'),s.first_line,s.last_line,json_extract(r.value,'$[4]')))
        FROM json_each(?) r
        JOIN spans s ON s.rowid=json_extract(r.value,'$[1]')
        JOIN docs d ON d.id=s.doc AND d.id=json_extract(r.value,'$[2]')
          AND d.digest=json_extract(r.value,'$[3]')
        JOIN names n ON n.name=d.name LEFT JOIN source_status st ON st.doc=d.id
        WHERE n.anchor=(SELECT anchor FROM names WHERE name=?)
          AND instr(s.body,json_extract(r.value,'$[4]'))>0 GROUP BY d.id
    """
    try:
        found = {}
        for path, digest, raw, status, records in db.execute(sql, (json.dumps(requests), project)):
            if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != digest:
                continue
            try:
                lines = raw.decode("utf-8-sig").splitlines(keepends=True)
            except UnicodeError:
                continue
            for citation, first, last, quote in json.loads(records):
                first, last = int(first), int(last)
                if not 1 <= first <= last <= len(lines):
                    continue
                original = "".join(lines[first-1:last])
                if quote not in original:
                    continue
                whole = len(original.encode()) <= 900
                row = {"file": Path(path).name, "lines": [first, last], "source_status":status or "archived snapshot",
                       "coverage": "whole section" if whole else "cited excerpt only"}
                if whole and original.strip() != quote.strip():
                    row["section"] = original.strip()
                found[citation, quote] = row
        return found
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc) and "no such column: d.data" not in str(exc):
            raise
        return {}


def summary(db, home, project, concepts=(), supplements=None):
    # Use the ledger's own resolution logic on this connection, inside the same
    # transaction as the recorded revision. Avoid a second, racing snapshot.
    store = runtime(home).Memory.from_connection(home, project, db)
    topics = db.execute(
        """SELECT concept FROM memory_claims WHERE project=? UNION
        SELECT child FROM memory_topics WHERE project=? ORDER BY 1 LIMIT ?""",
        (project, project, MAX_CONCEPTS + 1),
    ).fetchall()
    facts = []
    remaining = 7500
    omitted = len(topics) > MAX_CONCEPTS
    for row in topics[:MAX_CONCEPTS]:
        c = store.governing(row[0], 2**63 - 1)
        if c:
            if len(c["value"]) > remaining:
                omitted = True
                continue
            remaining -= len(c["value"])
            facts.append(
                dict(
                    concept=row[0],
                    value=c["value"],
                    from_edition=c["edition"],
                    revision=c["event"],
                )
            )
    questions = db.execute(
        """SELECT json_extract(j.value,'$.name') AS concept,
        json_extract(j.value,'$.question'),max(e.id) AS updated
        FROM memory_events e,json_each(e.payload) j WHERE project=?
        AND json_extract(j.value,'$.op')='investigate' GROUP BY concept
        ORDER BY updated DESC LIMIT 4""",
        (project,),
    ).fetchall()
    briefs = []
    for concept in dict.fromkeys(concepts):
        view = store.investigation(concept)
        briefs.append(
            dict(
                concept=concept, question=view["question"],
                scope=view["scope"],
                current=(view["current"] or {}).get("value"),
                theses=[(t["position"], t["status"]) for t in view["alternatives"][:6]],
                evidence=[
                    {k: e[k] for k in ("kind", "excerpt", "limitation", "citation")}
                    for e in view["evidence"][-4:]
                ],
            )
        )
        # Carry the reason and unresolved disagreements across sessions without expanding every exhibit.
        if view["lineage"]:
            governing_id = (view["current"] or {}).get("id")
            governing = next((r for r in view["lineage"] if r["id"] == governing_id), {})
            if governing.get("reason"):
                briefs[-1]["decision_basis"] = {k: governing[k] for k in ("reason","basis_exhibits")}
        positions = []
        for thesis in view["alternatives"][:6]:
            observations = thesis["observations"]
            nearest = [next((o for o in reversed(observations) if o["direction"] == direction), None)
                       for direction in ("for","against")]
            if any(nearest):
                positions.append({"thesis":thesis["name"],"observations":[o for o in nearest if o]})
        if positions:
            briefs[-1]["evidence_by_thesis"] = positions
        if view["working_assessments"]:
            briefs[-1]["working_assessments"] = view["working_assessments"][-3:]
        if view["check_results"]:
            briefs[-1]["last_check_result"] = view["check_results"][-1]
        if view["candidate_checks"]:
            briefs[-1]["candidate_checks"] = view["candidate_checks"][:3]
        if supplements is not None:
            briefs[-1]["exhibits_outside_window"] = max(0, len(view["evidence"]) - 36)
            supplements.append((briefs[-1], view["evidence"][-36:]))
        if len(briefs) == 3:
            break
    return dict(
            project=project,
            revision=head(db, project),
            latest_general_decisions=facts,
            investigation_questions=[
                dict(concept=row[0], question=row[1][:400]) for row in questions
            ],
            investigation_briefs=briefs,
            truncated=omitted,
    )


COMPARISON_REQUEST = re.compile(
    r"\b(?:nearby|compar(?:e|es|ed|ing|ison|isons)|contrasts?|alternatives?|"
    r"neighbou?ring|side[- ]by[- ]side|both (?:results|reports|observations))\b", re.I)


def comparison_sources(search, candidates, visible):
    """Rank additional verified excerpts locally. Keep each observation with its limits."""
    if not COMPARISON_REQUEST.search(search):
        return []
    generic = {"with", "which", "what", "were", "that", "from", "this", "does",
               "nearby", "compare", "observations", "results", "should", "record"}
    words = set(re.findall(r"[^\W_]{4,}", search.casefold())) - generic
    seen = {(item["citation"], item["excerpt"]) for item in visible}
    ranked = []
    for item in reversed(candidates):
        key = (item["citation"], item["excerpt"])
        if key in seen or len(json.dumps(item, ensure_ascii=False).encode("utf-8")) > 1200:
            continue
        seen.add(key)
        score = len(words.intersection(re.findall(r"[^\W_]{4,}", item["excerpt"].casefold())))
        if score >= 2:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in ranked[:2]]


def display_citations(brief):
    """Keep integrity metadata in the ledger; cite verified snapshots by stable IDs."""
    verified_ids = {entry["id"] for entry in brief["record_lookup"] if "id" in entry}
    exhibits = [*brief["evidence"], *brief.get("comparison_sources", [])]
    exhibits.extend(brief[k] for k in ("older_source", "background_source") if k in brief)
    for exhibit in exhibits:
        identifier = exhibit["citation"].partition(":sha256:")[0]
        if identifier in verified_ids and exhibit.get("verification") != "unverified":
            exhibit["citation"] = identifier


def shadow_source(search, project, revision, supplements, checked):
    records = []
    conditions = requested_conditions(search)
    unseen = []
    compare = COMPARISON_REQUEST.search(search)
    query_words = set(re.findall(r"[^\W_]{4,}", search.casefold()))
    for brief, evidence in supplements:
        visible = {e["citation"] for e in brief["evidence"]}
        visible.update(e["citation"] for e in brief.get("comparison_sources", []))
        for key in ("older_source", "background_source"):
            if key in brief:
                visible.add(brief[key]["citation"])
        for item in evidence:
            hit = checked.get((item["citation"], item["excerpt"]))
            if hit is None:
                status = "unverified"
            elif explicitly_excluded(item, search):
                status = "excluded subject or condition"
            else:
                # A limitation may mention the missing condition solely to exclude it.
                positive = {**item, "excerpt": hit.get("section", item["excerpt"]),
                            "limitation": ""}
                status = ("condition in observed text" if conditions and
                          all(condition_supported(positive, c) for c in conditions)
                          else "scope needs confirmation")
            records.append({"citation": item["citation"], "scope": status})
            if compare and hit and item["citation"] not in visible:
                terms = set(re.findall(r"[^\W_]{4,}", hit.get("section", item["excerpt"]).casefold()))
                if len(query_words & terms) >= 2:
                    unseen.append(item["citation"])
    return {"project": project, "revision": revision, "request_sha256": shadow.digest(search),
            "records": records, "unshown_comparisons": unseen, "direct_candidate": any(
                row["scope"] == "condition in observed text" for row in records)}


def ledger_for_research_project(db, home, name):
    names = [r[0] for r in db.execute("SELECT DISTINCT project FROM memory_events")]
    if name in names:
        return name
    aliases = [n for n in names if research_routing.name_key(n) == research_routing.name_key(name)]
    if len(aliases) == 1 and research_routing.unique_name(home, name):
        return aliases[0]
    return None


def on_turn(session_id="", turn_id="", user_message="", **_):
    if not session_id or not turn_id:
        return None
    home = Path(get_hermes_home())
    text = (
        user_message
        if isinstance(user_message, str)
        else " ".join(
            x.get("text", "")
            for x in user_message
            if isinstance(x, dict) and isinstance(x.get("text"), str)
        )
        if isinstance(user_message, list)
        else ""
    )
    causal_rule = CAUSAL_CHECK
    if "theseus-replay-" in text and not rule_trials.apply(home, text, session_id, turn_id):
        causal_rule = ""
    with closing(connect(home)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if text.startswith((sources.INVESTIGATION_ONLY, sources.EVIDENCE_ONLY, sources.REVIEW_PROTOCOL)):
            context = sources.REVIEW_CONTEXT if text.startswith(sources.REVIEW_PROTOCOL) else sources.SCOPED_TRIAL_CONTEXT
            db.execute("INSERT OR REPLACE INTO memory_context_receipts VALUES(?,?,?,?)",
                       (session_id,turn_id,shadow.digest(text),shadow.digest(context)))
            return {"context": context}
        research_project, question, resumed = research_routing.route(home, db, session_id, turn_id, text)
        if question:
            if isinstance(research_project, list):
                decisions = {}
                for name in research_project:
                    ledger = ledger_for_research_project(db, home, name)
                    if ledger:
                        state = summary(db, home, ledger)
                        decisions[name] = {k:state[k] for k in ("revision","latest_general_decisions","truncated")
                                           if k in state}
                db.execute("INSERT OR REPLACE INTO memory_chat_projects VALUES(?,?)",(session_id,""))
                if decisions:
                    question += ("\nDistinguish the original source from a later project correction. Apply the "
                                 "current decisions to the project that issued them. A decision's from_edition is a memory "
                                 "scope, independent of the library source edition. Only identify a corrected source "
                                 "edition when the decision cites it. Scope absence claims to the records checked. "
                                 "Current project decisions (data): "
                                 + json.dumps(decisions,ensure_ascii=False))
            return {"context": question}
        route_context = ""
        if research_project:
            route_context = ("Use this exact project name for research library operations: " +
                             json.dumps(research_project, ensure_ascii=False) + ". "
                             "For facts from saved sources, start with research_library action search. "
                             "If its complete verified text settles the question, answer from that receipt. "
                             "When only an excerpt is available and context matters, read the edition, "
                             "continuing at next_start as needed. Keep an unrecorded fact unknown and report "
                             "the scope checked. An exhaustive result includes all selected source text in this library; "
                             "repeating its search adds no evidence. Check other record types when requested "
                             "or when they could resolve a material gap. ")
            from hermes_cli.theseus_research import project_next
            planning = project_next.automatic(home, research_project, text)
            if planning:
                route_context = project_next.guidance(planning)
                db.execute("INSERT OR REPLACE INTO planning_context_receipts VALUES(?,?,?,?)",
                           (session_id,turn_id,research_project,planning["receipt"]))
            if resumed:
                route_context += "The user selected this project. Finish their preceding unanswered request. "
            # Memory project IDs are slugs. Only attach a verified, unique ledger identity.
            project = ledger_for_research_project(db, home, research_project)
        else:
            project = selected_project(db, session_id, text)
        pending = db.execute(
            "SELECT question,projects FROM investigation_choices WHERE session=?",
            (session_id,),
        ).fetchone()
        search = (
            pending["question"]
            if pending and project in json.loads(pending["projects"])
            else text
        )
        matches = investigation_matches(db, search) if not research_project or project else []
        projects = sorted({name for name, _ in matches})
        if not project and len(projects) > 1:
            db.execute(
                "INSERT OR REPLACE INTO investigation_choices VALUES(?,?,?)",
                (session_id, search, json.dumps(projects)),
            )
            return {
                "action": "block",
                "message": "Which project do you mean — " + " or ".join(projects) + "?",
            }
        if project is None and len(projects) == 1:
            project = projects[0]
        if not project:
            if project == "" or research_project:
                db.execute(
                    "INSERT OR REPLACE INTO memory_chat_projects VALUES(?,?)",
                    (session_id, ""),
                )
            return {
                "context": route_context + GUIDANCE + "\n" + RETRY_GUIDANCE + "\n" + causal_rule
                + (
                    " Multiple project references matched. Ask which investigation the user means before using evidence."
                    if project == ""
                    else ""
                )
            }
        db.execute(
            "INSERT OR REPLACE INTO memory_chat_projects VALUES(?,?)",
            (session_id, project),
        )
        db.execute("DELETE FROM investigation_choices WHERE session=?", (session_id,))
        concepts = project_investigations(db,project,search,matches)
        supplements = []
        data = summary(db, home, project, concepts, supplements)
        checked = lookup_exhibits(db, project, supplements)
        db.execute(
            "INSERT OR REPLACE INTO memory_turn_reads VALUES(?,?,?,?,NULL)",
            (session_id, turn_id, project, head(db, project)),
        )
    for brief, evidence in supplements:
        for thesis in brief.get("evidence_by_thesis",[]):
            for observation in thesis["observations"]:
                citation = observation["citation"]
                if citation.startswith("record:"):
                    checked_source = checked.get((citation,observation["evidence"]))
                    if checked_source and checked_source["source_status"]=="current":
                        observation["basis"] = "source observation; direction is an interpretation"
                    else:
                        observation.pop("evidence",None)
                        observation["basis"] = "historical annotation; current source support unverified"
                else:
                    observation["basis"] = "reported observation"
        receipt = []
        candidates = []
        for i, item in enumerate(evidence):
            citation = item["citation"]
            record = checked.get((citation, item["excerpt"]))
            if record is None:
                receipt.append({"citation": citation, "coverage": "unverified"})
                if i >= len(evidence) - 4:
                    brief["evidence"][i - len(evidence)] = {"citation": citation, "verification": "unverified"}
                continue
            entry = {k: v for k, v in record.items() if k != "section"}
            entry["id"] = citation.split(":sha256:")[0]
            receipt.append(entry)
            if record["source_status"] in ("superseded","conflict"):
                if i >= len(evidence) - 4:
                    brief["evidence"][i-len(evidence)]["source_status"] = record["source_status"]
                continue
            if i < len(evidence) - 4:
                if "section" in record:
                    expanded = {**item, "excerpt": record["section"]}
                    if len(json.dumps(expanded, ensure_ascii=False).encode("utf-8")) <= 1200:
                        item = expanded
                    else:
                        entry["coverage"] = "cited excerpt only"
                candidates.append(item)
            elif "section" in record:
                brief["evidence"][i - len(evidence)]["source_section"] = record["section"]
        brief["record_lookup"] = receipt
        conditions = requested_conditions(search)
        eligible = [item for item in candidates
                    if not explicitly_excluded(item, search)
                    and all(condition_supported(item, c) for c in conditions)]
        older = older_exhibit(eligible + evidence[-4:], search, brief["question"])
        if older:
            brief["older_source"] = older
        elif candidates:
            nearby = older_exhibit(candidates + evidence[-4:], search, brief["question"])
            if nearby:
                brief["background_source"] = nearby
        visible = brief["evidence"] + [brief[k] for k in ("older_source", "background_source") if k in brief]
        extras = comparison_sources(search, candidates, visible)
        if extras:
            brief["comparison_sources"] = extras
        brief["result_check"] = {
            "condition_phrases": conditions,
            "older_match": "candidate" if older else "no confirmed older result",
        }
    if supplements:
        shadow.propose(home, "source", project, session_id + "/" + turn_id,
                       lambda: shadow_source(search, project, data["revision"], supplements, checked))
    for brief in data["investigation_briefs"]:
        display_citations(brief)
    guidance = (
        (
            (
                "Use this project's current decision and investigation exhibits. Treat superseded source measurements as historical. The checked source sections and working assessments below are ready to use. For a question about a named file, read that file first and stop when it supplies the answer and its limits. Open other original evidence only for a missing detail that could change your answer. For read-only requests, use investigation_lookup or the investigation command; journal refreshes a file. A background_source "
                    "did not pass the literal condition screen. When comparing observations, include the "
                    "relevant comparison_sources alongside the other exhibits. Attribute each result to "
                    "its own subject and condition; combining neighboring observations cannot establish "
                    "the missing combination. " + SCOPE_GUIDANCE
                    + (" Original question: " + search if search != text else "")
            )
            if concepts
            else GUIDANCE
        )
        + "\n" + RETRY_GUIDANCE
        + ("\n" + INVESTIGATION_REASONING if concepts else "")
        + "\nFor an ordinary project correction, change project records only. If the harness fails, keep the job paused and report the failing operation; record a separate harness experiment before editing its implementation."
        + "\n" + causal_rule
    )
    payload = json.dumps(data,ensure_ascii=False,separators=(",",":"))
    full = guidance + "\nCurrent project state (data). For a specific edition, job exception, or historical question resolve that scope using the skill.\n" + payload
    if any("last_check_result" in brief for brief in data["investigation_briefs"]):
        from tools.hook_output_spill import get_spill_config
        spill = get_spill_config()
        if spill["enabled"] and len(full)>spill["max_chars"]:
            full = measured_handoff(payload)
    return {"context":route_context + full}


def measured_handoff(payload):
    return (
        MEASUREMENT_CHECK + " Use the current project snapshot below. Its receipt-checked source sections, decision and registered predictions are supplied. "
        "Read a named file when asked what it contains. Stop when the file supplies the answer and its limits. Answer directly when the supplied records settle a project question. Read an original once only for a material gap. "
        "State the governing decision separately from what the current scoped observations support. Cite the checked source, condition and receipt. "
        "Superseded readings are historical; independent conflicting readings or an untested condition leave the answer open. "
        "A check's predictions are conditional until the observed result is recorded. Start with the simplest distinguishable explanation; "
        "consider confounds, including contact, timing and run order. Keep controls fixed. Choose the feasible check that separates the live alternatives. "
        "On corrections, update project data only and keep the previous decision recoverable. "
        "For read-only questions, the journal command writes an index; use the provided state or a read-only lookup.\n"
        "Current project state (data).\n" + payload
    )


def before_tool(tool_name="", session_id="", turn_id="", api_request_id="", **_):
    if session_id and turn_id:
        ledger = Path(get_hermes_home()) / "project-recall/records.sqlite"
        if ledger.is_file():
            with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as reader:
                # A session started on an earlier release may not have this table yet.
                enabled = reader.execute("SELECT 1 FROM sqlite_master WHERE name='research_route_choices'").fetchone()
                if enabled and reader.execute(
                    "SELECT 1 FROM research_route_choices WHERE session=? AND turn=?", (session_id, turn_id)
                ).fetchone():
                    return {"action": "block", "message": "Ask for the project choice in chat and wait for the reply."}
    if (
        tool_name
        not in {"write_file", "patch", "terminal", "execute_code", "delegate_task"}
        or not session_id
        or not turn_id
    ):
        return None
    home = Path(get_hermes_home())
    try:
        with closing(connect(home)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            read = db.execute(
                "SELECT * FROM memory_turn_reads WHERE session=? AND turn=?",
                (session_id, turn_id),
            ).fetchone()
            if not read:
                return None
            latest = head(db, read["project"])
            if latest == read["revision"] and (
                not api_request_id or read["blocked_request"] != api_request_id
            ):
                return None
            data = summary(db, home, read["project"])
            db.execute(
                "UPDATE memory_turn_reads SET revision=?,blocked_request=? WHERE session=? AND turn=?",
                (latest, api_request_id, session_id, turn_id),
            )
            return {
                "action": "block",
                "message": "Project decisions changed during this turn. Reconcile the planned action with this snapshot and retry in a new model step. "
                + json.dumps(data, ensure_ascii=False),
            }
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {
            "action": "block",
            "message": f"Could not check project revisions before the action ({type(exc).__name__}). Retry the check before saving.",
        }


def shadow_answer(session_id="", turn_id="", assistant_response="", model="", **_):
    home = Path(get_hermes_home())
    journal = home / "shadow/history.sqlite"
    if not journal.is_file() or not isinstance(assistant_response, str):
        return
    with closing(sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
        event = session_id + "/" + turn_id
        row = db.execute("SELECT scope FROM decisions WHERE kind='source' AND event=?", (event,)).fetchone()
    if row:
        shadow.outcome(home, "source", row[0], event, lambda: {
            "session": session_id, "model": model, "sha256": shadow.digest(assistant_response),
            "scope": "finalized answer; content correctness requires review"})


def investigation_lookup(args, **_):
    try:
        report, _paths = sources.read(Path(get_hermes_home()), args)
        return json.dumps(report, ensure_ascii=False)
    except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        return json.dumps({"error": str(exc)})


def correction_impact(args, **_):
    """Open the existing ledger read-only, with a consistent snapshot across the comparisons."""
    try:
        home = Path(get_hermes_home())
        module = runtime(home)
        project = module.token(args["project"])
        path = home / "project-recall/records.sqlite"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            mem = module.Memory.from_connection(home, project, db)
            event = args.get("revision")
            if event is None:
                event = mem.rows("SELECT MAX(id) AS revision FROM memory_events WHERE project=?")[0]["revision"]
                if event is None:
                    raise ValueError("No recorded revision in this project")
            return json.dumps(mem.impact(event), ensure_ascii=False)
    except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        return json.dumps({"error":str(exc)})


def register(ctx):
    from hermes_cli.theseus_research import plugin as research
    research.register(ctx)
    ctx.register_tool(name=sources.TOOL, toolset="memory", schema=sources.SCHEMA,
                      handler=investigation_lookup, description=sources.SCHEMA["description"])
    description = "Trace a correction to changed decisions, dependent plans and exact lines in registered artifact jobs. Defaults to the project's latest revision. Read-only."
    schema = {"name":"correction_impact", "description":description, "parameters":{"type":"object",
              "properties":{"project":{"type":"string"},"revision":{"type":"integer","minimum":1}},
              "required":["project"],"additionalProperties":False}}
    ctx.register_tool(name="correction_impact", toolset="memory", schema=schema,
                      handler=correction_impact, description=description)
    ctx.register_hook("pre_llm_call", on_turn)
    ctx.register_hook("post_llm_call", shadow_answer)
    from hermes_cli.theseus_research.assistance import after_turn
    ctx.register_hook("post_llm_call", after_turn)
    ctx.register_hook("pre_tool_call", before_tool)
