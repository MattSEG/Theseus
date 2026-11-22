"""Find repeated reporting using explicit attribution or identical archived text."""

import re
from collections import defaultdict
from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode
from .library import search, accessible_edition

LABEL = r"(?:original (?:source|study|report)|primary source|press release|underlying study|based on)"
CITED = re.compile(r"\b" + LABEL + r"\s*[:=–-]?\s*(https?://[^\s<>\[\]\"']+)", re.I)


def normalized(url):
    try:
        p = urlsplit(url.rstrip(".,;!"))
    except ValueError:
        return ""
    if (
        p.scheme.lower() not in ("http", "https")
        or not p.hostname
        or p.username
        or p.password
        or not p.path.strip("/")
    ):
        return ""
    host = p.netloc.lower()
    if host in ("doi.org", "dx.doi.org"):
        return "https://doi.org/" + p.path.strip("/").lower()
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")
    ]
    return urlunsplit((p.scheme.lower(), host, p.path, urlencode(query), ""))


def operate(db, project, args):
    if args["action"] != "reporting_compare":
        raise ValueError("Unknown reporting operation.")
    sources = search(db, project, {"query": args.get("query", "")})["sources"]
    groups = defaultdict(dict)
    originals = defaultdict(list)
    all_items = {}
    for src in sources:
        s = accessible_edition(db, project, src["source"], src["edition"])
        sid = s["source"]
        body = s["body"]
        item = dict(source=sid, edition=s["edition"], title=s["title"])
        all_items[sid] = item
        own = normalized(s["origin"])
        if own:
            originals[own].append(item)
        for m in CITED.finditer(body):
            key = normalized(m.group(1))
            if key:
                groups[("Shared attributed origin", key)][sid] = dict(
                    item, basis=m.group(0)
                )
        if len(body.strip()) >= 40:
            groups[("Identical archived text", s["digest"])][sid] = dict(
                item, basis="Matching SHA-256 of full archived text"
            )
    output = []
    classified = set()
    for (kind, key), members in groups.items():
        if kind == "Shared attributed origin":
            for original in originals[key]:
                members.setdefault(
                    original["source"], dict(original, basis="Archived original URL")
                )
        if len(members) < 2:
            continue
        classified.update(members)
        output.append(dict(kind=kind, key=key, members=list(members.values())))
    return dict(
        reviewed=len(sources),
        groups=output,
        unclassified=[v for k, v in all_items.items() if k not in classified],
        scope="Up to 50 matching originals plus pinned editions. A shared reference establishes common attribution, not independent corroboration.",
    )
