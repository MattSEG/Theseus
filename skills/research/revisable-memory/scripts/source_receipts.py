"""Validate an archived source independently of its search index."""

import hashlib
from pathlib import Path
import re

CITATION = re.compile(r"record:(\d{1,18}):snapshot:(\d{1,18}):sha256:([a-f0-9]{64})")


def source(db, project, citation, excerpt):
    """Check scope, content hash, indexed coordinates and text against original bytes."""
    match = CITATION.fullmatch(citation)
    if not match or not excerpt.strip():
        raise ValueError("invalid source citation or empty excerpt")
    row = db.execute(
        """
        SELECT d.path,d.data,s.first_line,s.last_line,s.body
        FROM spans s JOIN docs d ON d.id=s.doc JOIN names n ON n.name=d.name
        WHERE s.rowid=? AND d.id=? AND d.digest=? AND n.anchor=(
          SELECT anchor FROM names WHERE name=?)""",
        (*match.groups(), project),
    ).fetchone()
    if (
        not row
        or not isinstance(row[1], bytes)
        or hashlib.sha256(row[1]).hexdigest() != match[3]
    ):
        raise ValueError("source snapshot, digest or project does not match")
    lines = row[1].decode("utf-8-sig").splitlines(keepends=True)
    first, last = int(row[2]), int(row[3])
    if not 1 <= first <= last <= len(lines):
        raise ValueError("source coordinates outside snapshot")
    section = "".join(lines[first - 1 : last])
    if section != row[4] or excerpt not in section:
        raise ValueError("indexed excerpt disagrees with original source bytes")
    # Context must include adjacent qualifiers. Oversized sections remain explicitly partial.
    if len(section) <= 3000:
        start, end = 0, len(section)
    else:
        if len(excerpt) > 2000:
            raise ValueError("excerpt exceeds bounded inspection window")
        offset = section.index(excerpt)
        start, end = (
            max(0, offset - 400),
            min(len(section), offset + len(excerpt) + 400),
        )
    return {
        "citation": citation,
        "file": Path(row[0]).name,
        "section_lines": [first, last],
        "coverage": "whole section"
        if (start, end) == (0, len(section))
        else "partial section",
        "context": section[start:end],
        "excerpt": excerpt,
    }, row[0]


def fields(text):
    """Index original field lines; retain duplicates so ambiguity cannot disappear."""
    found = {}
    for line in text.splitlines():
        label, sep, value = line.partition(": ")
        if sep and label.strip() == label and len(label) <= 100 and value.strip():
            found.setdefault(label, []).append(value)
    return found


def scope_field_match(target, section, field, replacement, subject, required):
    card = fields(target)
    # A blank line separates records. A second occurrence of a field in one
    # record is ambiguous even when the two values happen to be identical.
    blocks = [
        block
        for block in re.split(r"\n\s*\n", section.replace("\r\n", "\n"))
        if replacement in block.splitlines()
    ]
    if len(blocks) != 1:
        raise ValueError("replacement does not identify a unique source record")
    observed = fields(blocks[0])
    if observed.get(field) != [replacement.partition(": ")[2]]:
        raise ValueError("source record has competing values for the edited field")
    for label in (card.keys() & observed.keys()) - {field}:
        if card[label] != observed[label] or len(card[label]) != 1:
            raise ValueError("source and target disagree on field: " + label)
    # Legacy cards have no declared identity fields. Require all labelled
    # siblings until a scope is declared, rather than guessing which qualify the result.
    labels = required if required is not None else sorted(card.keys() - {field})
    if field in labels:
        raise ValueError("a scope field cannot be replaced by a measurement edit")
    if not labels or any(
        len(card.get(k, [])) != 1 or observed.get(k) != card[k] for k in labels
    ):
        raise ValueError("source did not record all required target scope fields")
    if not any(subject in (card[k][0], k + ": " + card[k][0]) for k in labels):
        raise ValueError("subject must identify an unchanged, matched scope field")


def scoped_value(section, scope, field):
    """Read one field from one complete record with all the requested scope coordinates."""
    matches = []
    for block in re.split(r"\n[ \t]*\n", section.replace("\r\n", "\n")):
        values = fields(block)
        if all(values.get(label) == [expected] for label, expected in scope.items()):
            matches.append(values)
    if len(matches) != 1:
        return None, "no unique record matched the requested scope"
    values = matches[0].get(field, [])
    if len(values) != 1:
        return None, "no unique measured field in the matching record"
    return values[0], "recorded"


def field_label(label):
    # Fold title case on ordinary words. Symbols, acronyms and mixed-case units
    # are significant (for example mW and MW), as are all measured values.
    return " ".join(
        part.lower() if re.fullmatch(r"[A-Za-z][a-z]{2,}", part) else part
        for part in label.split(" ")
    )


def canonical_claim(query):
    scope = {field_label(k): v for k, v in query["scope"].items()}
    field = field_label(query["field"])
    if len(scope) != len(query["scope"]) or field in scope:
        raise ValueError("ambiguous scope labels or measured endpoint")
    return {**query, "field": field, "scope": scope}


def claim_support(sources, query, *, multiple=True, normalized=False):
    if not isinstance(query, dict) or set(query) - {"scope", "field", "value"}:
        raise ValueError("select an observed field and its scope")
    field, scope = query.get("field"), query.get("scope")
    if (
        not isinstance(field, str)
        or not field.strip()
        or len(field) > 100
        or "\n" in field
        or "\r" in field
    ):
        raise ValueError("invalid observed field")
    if "value" in query and (
        not isinstance(query["value"], str) or not query["value"].strip()
    ):
        raise ValueError("invalid proposed field value")
    if (
        not isinstance(scope, dict)
        or not 1 <= len(scope) <= 8
        or field in scope
        or not all(
            isinstance(k, str)
            and isinstance(v, str)
            and k.strip()
            and v.strip()
            and len(k + v) <= 300
            for k, v in scope.items()
        )
    ):
        raise ValueError("supply distinct subject or condition fields")
    if normalized:
        canonical = canonical_claim(query)
        scope, field = canonical["scope"], canonical["field"]
    ambiguous_labels = set()
    matched = []
    for source in sources:
        if source["coverage"] != "whole section" or source.get("source_status") in (
            "superseded",
            "conflict",
        ):
            continue
        if multiple:
            # Two actual readings in the same archived section can disagree.
            # A scoped field edit still requires the stricter unique record.
            blocks = re.split(r"\n[ \t]*\n", source["context"].replace("\r\n", "\n"))
            readings = []
            for block in blocks:
                indexed = fields(block)
                if normalized:
                    canonical_fields = {}
                    seen = set()
                    for original, readings_here in indexed.items():
                        name = field_label(original)
                        if name in canonical_fields and (
                            name in scope or name == field
                        ):
                            seen.add(name)
                        canonical_fields[name] = readings_here
                    if seen:
                        ambiguous_labels.update(seen)
                        continue
                    indexed = canonical_fields
                if all(
                    indexed.get(key) == [expected] for key, expected in scope.items()
                ):
                    readings.extend(indexed.get(field, []))
        else:
            value, _ = scoped_value(source["context"], scope, field)
            readings = [value] if value is not None else []
        for value in readings:
            row = {"citation": source["citation"], "value": value}
            if row not in matched:
                matched.append(row)
    values = {item["value"] for item in matched}
    status = "no matching result recorded in the inspected sources"
    if len(values) > 1:
        status = "conflicting recorded values"
    elif values:
        status = (
            "recorded"
            if "value" not in query or query["value"] in values
            else "different value recorded"
        )
    result = {"requested": query, "status": status, "measurements": matched}
    if ambiguous_labels:
        result["ambiguous_labels"] = sorted(ambiguous_labels)
        result["status"] = "ambiguous source field labels"
    return result


def snapshot_status(db, project, path, digest, cursor):
    if cursor is None:
        return "archived snapshot"
    latest = db.execute(
        """WITH latest AS (
        SELECT max(a.id) AS activation FROM activations a JOIN docs d ON d.id=a.doc
        JOIN names n ON n.name=d.name WHERE d.path=? AND a.id<=?
        AND n.anchor=(SELECT anchor FROM names WHERE name=?) GROUP BY d.name)
        SELECT DISTINCT d.digest FROM latest l JOIN activations a ON a.id=l.activation
        JOIN docs d ON d.id=a.doc""",
        (path, cursor, project),
    ).fetchall()
    if len(latest) > 1:
        return "conflict"
    return "current" if latest and latest[0][0] == digest else "superseded"
