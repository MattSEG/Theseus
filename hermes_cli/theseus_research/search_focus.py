"""Bounded, source-preserving excerpts for library search experiments."""

import hashlib
import re
from itertools import islice

VERSION = "qualified-passages-6"
EXCERPT_CHARS = 220
FILLER = frozenset(
    "a an the i me my we our you your he she it its they their this that these those "
    "is are was were be been being am do does did have has had of for by with in on "
    "at and or please what which who how".split()
)


def anchor_positions(body, folded, anchor, count):
    if body.isascii() and count <= 64:
        start = 0
        hits = 0
        while hits < 16:
            pos = folded.find(anchor, start)
            if pos < 0:
                break
            start = pos + 1
            after = pos + len(anchor)
            left = body[pos - 1] if pos else ""
            right = body[after : after + 1]
            if left.isalnum() or left == "_" or right.isalnum() or right == "_":
                continue
            yield pos
            hits += 1
        return
    # Unicode case folding can change length; regex positions refer to the original text.
    pattern = re.compile(r"(?<!\w)" + re.escape(anchor) + r"(?!\w)", re.IGNORECASE)
    for match in islice(pattern.finditer(body), 16):
        yield match.start()


def excerpt_start(body, terms, *, folded=None):
    """Prefer a small window containing several query terms. Return a real text offset."""
    if len(body) <= EXCERPT_CHARS or not terms:
        return 0
    words = list(
        dict.fromkeys(
            t.casefold() for t in terms if t.casefold() not in FILLER or t.isupper()
        )
    )
    if not words:
        return 0
    if folded is None:
        folded = body.casefold()
    counts = {w: folded.count(w) for w in words}
    opening = body[:EXCERPT_CHARS].casefold()
    uncovered = {w for w in words if w not in opening}
    anchors = sorted((w for w in words if counts[w]), key=lambda w: (w not in uncovered, counts[w]))[:3]
    if not anchors:
        return 0
    positions = {0}
    last = max(0, len(body) - EXCERPT_CHARS)
    for anchor in anchors:
        for pos in anchor_positions(body, folded, anchor, counts[anchor]):
            left = max(0, pos - EXCERPT_CHARS // 2)
            # A line or sentence boundary usually preserves the complete assertion.
            line = body.rfind("\n", left, pos) + 1
            sentence = body.rfind(". ", left, pos)
            start = max(left, line, sentence + 2 if sentence >= 0 else 0)
            positions.add(min(last, start))

    def relevance(start):
        span = body[start : start + EXCERPT_CHARS].casefold()
        return (sum(w in span for w in uncovered), sum(w in span for w in words), -start)

    return max(positions, key=relevance)


QUANTITY_TAIL = re.compile(
    r"(?<!\w)[+−-]?\d+(?:[.,]\d+)*(?:\s*(?:[%°µμ/$]|[^\W\d_]){1,5})?$"
)
END_OF_ASSERTION = re.compile(r"[.!?](?=\s|$)|\n")
EXTRA_BYTES = 384
MAX_CONTINUATION = 120
MAX_SOURCE_CHECKS = 6


def finish_local_quantities(db, rows):
    """Extend only a uniquely located, clipped measurement in a verified edition."""
    remaining = EXTRA_BYTES
    checks = 0
    for row in rows:
        if checks == MAX_SOURCE_CHECKS or not remaining:
            break
        snippet = row["excerpt"]
        if row["short_text"] is not None or not snippet.endswith("…"):
            continue
        prefix = snippet[:-1]
        if not QUANTITY_TAIL.search(prefix[-32:]):
            continue
        anchor = prefix.removeprefix("…")
        if not anchor:
            continue
        checks += 1
        body = db.execute(
            "SELECT body FROM editions WHERE id=?", (row["edition"],)
        ).fetchone()[0]
        if hashlib.sha256(body.encode()).hexdigest() != row["digest"]:
            row.update(excerpt="", evidence_scope="edition receipt mismatch")
            continue
        start = body.find(anchor)
        if start < 0 or body.find(anchor, start + 1) >= 0:
            continue
        end = start + len(anchor)
        tail = body[end : end + MAX_CONTINUATION + 1]
        match = END_OF_ASSERTION.search(tail)
        if match:
            take = match.end()
        elif end + len(tail) == len(body):
            take = len(tail)
        else:
            continue
        if take > MAX_CONTINUATION:
            continue
        continuation = tail[:take].rstrip("\n")
        amount = len(continuation.encode())
        if amount and amount <= remaining:
            row["excerpt"] = (
                prefix
                + continuation
                + ("…" if end + len(continuation) < len(body) else "")
            )
            row["excerpt_start"] = start
            remaining -= amount


SUPPLEMENT_BYTES = 960
MAX_SUPPLEMENTS = 4


def attach_passages(rows):
    remaining = SUPPLEMENT_BYTES
    count = deferred = 0
    for row in rows:
        match = row.pop("_matched", None)
        if not match or row["evidence_scope"] != "excerpt only":
            continue
        start, text = match
        raw = text.encode()
        if count >= MAX_SUPPLEMENTS or len(raw) > remaining:
            row["matched_passage_deferred"] = True
            deferred += 1
            continue
        row["matched_passage"] = dict(
            start=start,
            end=start + len(text),
            text=text,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        remaining -= len(raw)
        count += 1
    return (
        dict(
            additional_passage_bytes=SUPPLEMENT_BYTES - remaining,
            deferred_passages=deferred,
        )
        if count or deferred
        else {}
    )


def matched_text(row, body=None):
    match = row.get("matched_passage")
    if not isinstance(match, dict):
        return ""
    text, start, end = match.get("text"), match.get("start"), match.get("end")
    if (
        not isinstance(text, str)
        or not text
        or len(text) > EXCERPT_CHARS
        or type(start) is not int
        or type(end) is not int
        or start < 0
        or end != start + len(text)
        or hashlib.sha256(text.encode()).hexdigest() != match.get("sha256")
    ):
        return ""
    if body is not None and body[start:end] != text:
        return ""
    return text
