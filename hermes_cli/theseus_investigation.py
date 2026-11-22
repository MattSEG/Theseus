"""Read-only investigation evidence, replayable from frozen project snapshots."""

from contextlib import closing
from importlib.util import module_from_spec, spec_from_file_location
import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,79}")


def skill_module(name):
    # The desktop launcher exposes hermes_cli, while the bundled skills directory
    # is data and may be absent from sys.path in a proof subprocess.
    path = (
        Path(__file__).resolve().parents[1]
        / "skills/research/revisable-memory/scripts"
        / (name + ".py")
    )
    spec = spec_from_file_location("theseus_" + name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checks = skill_module("investigation_checks")
receipts = skill_module("source_receipts")
EVIDENCE_ONLY = (
    "Source protocol for this trial: make exactly one read_file call on the record named below. "
    "Use only the returned text. Skip skills, project lookup, terminal, and other tools.\n\n"
)
INVESTIGATION_ONLY = (
    "Source protocol for this trial: consult only the native investigation_lookup tool. "
    "Use the original records it returns; if unavailable, report the gap.\n\n"
)
TRIAL_CONTEXT = "Evidence-only transfer trial. Project conclusions, saved outputs and earlier conversation decisions are withheld. Follow the user's source protocol."
SCOPED_TRIAL_CONTEXT = TRIAL_CONTEXT + (
    " Bind results to the subject, condition and period the source identifies. If a different subject is unmentioned, "
    "whether it was tested remains unknown. A categorical exclusion needs an explicit statement or an exhaustive, "
    "identifiable roster. Preserve explicit negatives when the source does give one."
)
REVIEW_PROTOCOL = "Blinded Theseus reviewer study, version 2. "
REVIEW_CONTEXT = "Read only the supplied anonymous answers. The projects, previous decisions, reviewer scores and known outcomes are withheld."


def isolated_context(home, sid, message, context):
    path = Path(home) / "project-recall/records.sqlite"
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            if db.execute(
                "SELECT 1 FROM memory_turn_reads WHERE session=? LIMIT 1", (sid,)
            ).fetchone():
                return "ordinary project context supplied"
            rows = db.execute(
                """SELECT DISTINCT request_digest,context_digest FROM memory_context_receipts
                                 WHERE session=?""",
                (sid,),
            ).fetchall()
    except sqlite3.Error:
        return "context provenance unavailable"
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    if rows == [(sha(message), sha(context))]:
        return "verified"
    return "no matching isolation receipt"


TOOL = "investigation_lookup"
SCHEMA = {
    "name": TOOL,
    "description": "Read original evidence for a named Theseus investigation. Returns source lines and snapshot receipts. Use these for factual support; an interpretation or causal explanation requires a separate inference.",
    "parameters": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Stable project identifier"},
            "concept": {"type": "string", "description": "Investigation identifier"},
            "claim": {
                "type": "object",
                "description": "Optional measured-field question for structured records. Scope and endpoint must occur together. Narrative evidence may require interpretation.",
                "properties": {
                    "field": {"type": "string"},
                    "scope": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "value": {"type": "string"},
                },
                "required": ["field", "scope"],
                "additionalProperties": False,
            },
            "record": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional specific earlier source record. By default read the four most recent exhibits.",
            },
        },
        "required": ["project", "concept"],
        "additionalProperties": False,
    },
}


def description_digest(value):
    # Provider adapters may expand implicit, empty JSON Schema object defaults.
    # Normalize only these two schema keywords; retain descriptions, constraints and all tool names.
    def normalize(schema):
        if not isinstance(schema, dict):
            return
        if schema.get("type") == "object":
            if schema.get("required") == []:
                schema.pop("required")
            if schema.get("properties") == {}:
                schema.pop("properties")
        for child in schema.get("properties", {}).values():
            normalize(child)
        for key in ("items", "additionalProperties"):
            normalize(schema.get(key))

    value = copy.deepcopy(value)
    if isinstance(value, dict) and isinstance(value.get("tools"), dict):
        for tool in value["tools"].values():
            if isinstance(tool, dict):
                normalize(tool.get("parameters"))
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def description():
    return {"tools": {TOOL: {k: SCHEMA[k] for k in ("description", "parameters")}}}


def legacy_description_digest():
    # Initial native lookup receipts predate the optional measured-field question.
    initial = copy.deepcopy(description())
    initial["tools"][TOOL]["parameters"]["properties"].pop("claim", None)
    return description_digest(initial)


def archive_head(db, project):
    """Capture the archive independently of the investigation event stream."""
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='activations' AND type='table'"
    ).fetchone():
        return None
    return db.execute(
        """SELECT coalesce(max(a.id),0) FROM activations a JOIN docs d ON d.id=a.doc
                         JOIN names n ON n.name=d.name WHERE n.anchor=(SELECT anchor FROM names WHERE name=?)""",
        (project,),
    ).fetchone()[0]


def freeze_receipts(home, investigation, citations):
    """Enroll exact original snapshots, including retired versions sharing one disk path."""
    if not citations:
        return {}
    if not investigation or len(set(citations)) != len(citations):
        raise ValueError(
            "archived receipts need a named investigation and distinct citations"
        )
    project, concept = investigation
    result = {}
    path = Path(home) / "project-recall/records.sqlite"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        for citation in citations:
            if not receipts.CITATION.fullmatch(citation):
                raise ValueError("choose an exact original source receipt")
            row = db.execute(
                """SELECT e.evidence FROM memory_events e,json_each(e.payload) j
                WHERE e.project=? AND e.citation=? AND json_extract(j.value,'$.op')='exhibit'
                AND json_extract(j.value,'$.name')=? ORDER BY e.id DESC LIMIT 1""",
                (project, citation, concept),
            ).fetchone()
            if not row:
                raise ValueError(
                    "the original receipt is absent from the named investigation"
                )
            _, result[citation] = receipts.source(db, project, citation, row[0])
    return result


def read(
    home,
    args,
    *,
    revision=None,
    version=None,
    archive_cursor=None,
    allowed=None,
    allowed_receipts=None,
):
    if set(args) - {"project", "concept", "record", "claim"}:
        raise ValueError("unknown investigation lookup argument")
    project, concept = args["project"], args["concept"]
    if not all(
        isinstance(k, str) and IDENTIFIER.fullmatch(k) for k in (project, concept)
    ):
        raise ValueError("invalid project or investigation")
    if version is None:
        version = 5 if "claim" in args else 2
    bounded = version >= 3 and "claim" in args and "record" not in args
    if "claim" in args:
        receipts.claim_support(
            [], args["claim"], multiple=version >= 4, normalized=version >= 5
        )
    record = args.get("record")
    if "record" in args and (type(record) is not int or not 0 < record < 2**63):
        raise ValueError("invalid source record")
    path = Path(home) / "project-recall/records.sqlite"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        if revision is None:
            revision = db.execute(
                "SELECT max(id) FROM memory_events WHERE project=?", (project,)
            ).fetchone()[0]
        if (
            type(revision) is not int
            or not db.execute(
                "SELECT 1 FROM memory_events WHERE project=? AND id=?",
                (project, revision),
            ).fetchone()
        ):
            raise ValueError("unknown investigation revision")
        params = (project, revision, concept)
        base = """ FROM memory_events e,json_each(e.payload) j WHERE e.project=? AND e.id<=?
                    AND json_extract(j.value,'$.name')=? """
        if not db.execute(
            "SELECT 1"
            + base
            + "AND json_extract(j.value,'$.op')='investigate' LIMIT 1",
            params,
        ).fetchone():
            raise ValueError("unknown investigation")
        query = base + " AND json_extract(j.value,'$.op')='exhibit'"
        if record is not None:
            query += " AND e.citation LIKE ?"
            params = (*params, f"record:{record}:snapshot:%")
        window = 36 if bounded else 4
        rows = db.execute(
            "SELECT e.citation,e.evidence"
            + query
            + " ORDER BY e.id DESC,j.key DESC LIMIT ?",
            (*params, window + 1),
        ).fetchall()
        result = {
            "project": project,
            "concept": concept,
            "revision": revision,
            "earlier_entries_omitted": len(rows) > 4,
            "sources": [],
            "unverified": [],
        }
        if version >= 2:
            current_cursor = archive_head(db, project)
            if archive_cursor is None:
                archive_cursor = current_cursor
            elif (
                type(archive_cursor) is not int
                or current_cursor is None
                or not 0 <= archive_cursor <= current_cursor
            ):
                raise ValueError("invalid archive revision")
            result.update(receipt_version=version, archive_cursor=archive_cursor)
        paths, inspected, checked = {}, [], []
        display_older = 0
        for index, (citation, excerpt) in enumerate(rows[:window]):
            try:
                item, path = receipts.source(db, project, citation, excerpt)
            except (ValueError, UnicodeError):
                result["unverified"].append(citation)
                continue
            digest = receipts.CITATION.fullmatch(citation)[3]
            if (
                allowed is not None
                and allowed.get(path) != digest
                and (allowed_receipts or {}).get(citation) != path
            ):
                raise OutsidePlan("source version is outside the frozen plan")
            if version >= 2:
                item["source_status"] = receipts.snapshot_status(
                    db, project, path, digest, archive_cursor
                )
            paths[path] = digest
            if bounded:
                inspected.append(
                    {
                        k: item[k]
                        for k in ("citation", "file", "coverage", "source_status")
                    }
                )
                checked.append(item)
            if index < 4:
                result["sources"].append(item)
            elif (
                display_older < 2
                and receipts.claim_support(
                    [item],
                    args["claim"],
                    multiple=version >= 4,
                    normalized=version >= 5,
                )["measurements"]
            ):
                result["sources"].append(item)
                display_older += 1
        if "claim" in args:
            result["claim_check"] = receipts.claim_support(
                checked if bounded else result["sources"],
                args["claim"],
                multiple=version >= 4,
                normalized=version >= 5,
            )
        if bounded:
            result["search"] = {
                "limit": window,
                "inspected": inspected,
                "outside_window": len(rows) > window,
                "unverified_count": len(result["unverified"]),
                "older_passages_shown": display_older,
            }
            limited = [
                i["citation"]
                for i in inspected
                if i["coverage"] != "whole section" or i["source_status"] == "conflict"
            ]
            if len(rows) > window or result["unverified"] or limited:
                result["claim_check"]["limits"] = (
                    "Incomplete coverage; checked records cannot establish absence across the whole investigation."
                )
        return result, paths


class OutsidePlan(ValueError):
    """Replayed evidence is real but belongs outside the trial's approval set."""


def verify(home, args, returned, allowed, allowed_receipts=None):
    if not isinstance(returned, dict) or type(returned.get("revision")) is not int:
        raise ValueError("investigation result has no revision")
    version = returned.get("receipt_version", 1)
    if type(version) is not int or version not in (1, 2, 3, 4, 5):
        raise ValueError("unknown lookup receipt format")
    expected, paths = read(
        home,
        args,
        revision=returned["revision"],
        version=version,
        archive_cursor=returned.get("archive_cursor"),
        allowed=allowed,
        allowed_receipts=allowed_receipts,
    )
    if returned != expected or not paths or expected["unverified"]:
        raise ValueError(
            "investigation result does not replay against its original receipts"
        )
    entries = expected.get("search", {}).get("inspected", expected["sources"])
    return [s["citation"] for s in entries]
