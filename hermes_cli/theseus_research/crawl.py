"""Manually driven, bounded same-site crawls. Commit a single page and its frontier together."""

import ipaddress
import re
import socket
import ssl
import time
import uuid
from html.parser import HTMLParser
from urllib import robotparser
from urllib.parse import urljoin, urlsplit, urlunsplit

import urllib3

from .library import capture, field
from . import trail
from tools.url_safety import is_safe_url
from tools.website_policy import check_website_access

DDL = """
CREATE TABLE IF NOT EXISTS crawls(id INTEGER PRIMARY KEY, project TEXT NOT NULL, question TEXT NOT NULL,
 seed TEXT NOT NULL, pages INTEGER NOT NULL, depth INTEGER NOT NULL, seconds INTEGER NOT NULL,
 spent REAL NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'paused', robots TEXT,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS crawl_clock(crawl INTEGER PRIMARY KEY REFERENCES crawls(id), started REAL, next_fetch REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS crawl_pages(id INTEGER PRIMARY KEY, crawl INTEGER NOT NULL REFERENCES crawls(id),
 url TEXT NOT NULL, depth INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'queued', lease TEXT, expires REAL,
 final_url TEXT, source INTEGER, edition INTEGER, digest TEXT, detail TEXT, UNIQUE(crawl,url));
"""
AGENT = "TheseusResearch"
MAX_BYTES = 1_000_000


def url_key(url):
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port not in (None, 80, 443)
        or len(url) > 4096
    ):
        raise ValueError("Use an HTTP or HTTPS page on a public website.")
    return urlunsplit(
        (parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, "")
    )


class Page(HTMLParser):
    def __init__(self, base_url=""):
        self.base_url = base_url
        self.anchor = None
        super().__init__(convert_charrefs=True)
        self.lines, self.links, self.titles = [], [], []
        self.ignore = 0
        self.title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template", "noscript"):
            self.ignore += 1
        if self.ignore:
            return
        if tag == "title":
            self.title = True
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "section"):
            self.lines.append("\n")
        if tag == "a" and len(self.links) < 1500:
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
                self.anchor = (href, [])

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            from .reporting import LABEL, normalized
            import re

            href, parts = self.anchor
            label = " ".join("".join(parts).split())
            target = normalized(urljoin(self.base_url, href))
            if target and re.fullmatch(LABEL, label, re.I):
                self.lines.append(" [Original source: " + target + "] ")
            self.anchor = None
        if tag in ("script", "style", "template", "noscript") and self.ignore:
            self.ignore -= 1
        if tag == "title":
            self.title = False
        if tag in ("p", "div", "tr", "li", "h1", "h2", "h3", "section"):
            self.lines.append("\n")

    def handle_data(self, data):
        if not self.ignore:
            if self.anchor:
                self.anchor[1].append(data)
            self.lines.append(" ".join(data.split()) + " ")
            if self.title:
                self.titles.append(data)

    def text(self):
        return "\n".join(
            line.strip() for line in "".join(self.lines).splitlines() if line.strip()
        )


def fetch(url, host, deadline, rules=None):
    """Resolve and pin the connection address on each same-site redirect. Never attach credentials."""
    for _ in range(5):
        target = urlsplit(url_key(url))
        if (
            target.netloc.lower() != host
            or check_website_access(url)
            or not is_safe_url(url)
            or (rules is not None and not rules.can_fetch(AGENT, url))
        ):
            raise ValueError("URL is outside this crawl or blocked by website policy.")
        port = target.port or (443 if target.scheme == "https" else 80)
        ips = [
            r[4][0]
            for r in socket.getaddrinfo(target.hostname, port, type=socket.SOCK_STREAM)
        ]
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError("This crawl accepts public websites only.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Time budget reached.")
        opts = {
            "timeout": urllib3.Timeout(total=min(remaining, 8), read=min(remaining, 3)),
            "retries": False,
        }
        if target.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(
                ips[0],
                port,
                server_hostname=target.hostname,
                assert_hostname=target.hostname,
                ssl_context=ssl.create_default_context(),
                **opts,
            )
        else:
            pool = urllib3.HTTPConnectionPool(ips[0], port, **opts)
        with pool:
            response = pool.urlopen(
                "GET",
                urlunsplit(("", "", target.path or "/", target.query, "")),
                headers={
                    "Host": target.netloc,
                    "User-Agent": AGENT,
                    "Accept-Encoding": "identity",
                },
                redirect=False,
                preload_content=False,
            )
            try:
                status = response.status
                if status in (301, 302, 303, 307, 308):
                    next_url = response.headers.get("Location")
                    if not next_url:
                        raise ValueError("Redirect has no destination.")
                    url = url_key(urljoin(url, next_url))
                    continue
                if status != 200:
                    return status, url, "", ""
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].lower()
                )
                if content_type not in (
                    "text/html",
                    "application/xhtml+xml",
                    "text/plain",
                    "text/csv",
                    "application/json",
                ):
                    raise ValueError("Unsupported page content type.")
                chunks = []
                size = 0
                for chunk in response.stream(8192, decode_content=True):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("Page exceeds the 1 MB research limit.")
                    if time.monotonic() > deadline:
                        raise TimeoutError("Time budget reached.")
                    chunks.append(chunk)
                charset = re.search(
                    r"charset=([\w-]+)", response.headers.get("content-type", ""), re.I
                )
                encoding = charset.group(1) if charset else "utf-8"
                return (
                    status,
                    url,
                    content_type,
                    b"".join(chunks).decode(encoding, errors="replace"),
                )
            finally:
                response.close()
    raise ValueError("Too many redirects.")


def bounded(args, k, default, maximum, minimum=1):
    n = args.get(k, default)
    if type(n) is not int or not minimum <= n <= maximum:
        raise ValueError(f"{k} must be between {minimum} and {maximum}.")
    return n


def status(db, project, run):
    row = db.execute(
        "SELECT id,project,question,seed,pages,depth,seconds,spent,state FROM crawls WHERE project=? AND id=?",
        (project, run),
    ).fetchone()
    if not row:
        raise ValueError("No such crawl in this project.")
    result = dict(row)
    result["queue"] = [
        dict(r)
        for r in db.execute(
            "SELECT url,depth,state,source,edition,detail FROM crawl_pages WHERE crawl=? ORDER BY id LIMIT 250",
            (run,),
        )
    ]
    return result


def charge_elapsed(db, run):
    now = time.time()
    clock = db.execute(
        "SELECT started FROM crawl_clock WHERE crawl=?", (run,)
    ).fetchone()
    if clock and clock[0] is not None:
        db.execute(
            "UPDATE crawls SET spent=min(seconds,spent+?) WHERE id=?",
            (max(0, now - clock[0]), run),
        )
        db.execute("UPDATE crawl_clock SET started=? WHERE crawl=?", (now, run))


def step(db, project, run):
    db.execute("BEGIN IMMEDIATE")
    if db.execute(
        "SELECT 1 FROM crawls WHERE id=? AND project=? AND state='running'",
        (run, project),
    ).fetchone():
        charge_elapsed(db, run)
    job = db.execute(
        "SELECT * FROM crawls WHERE id=? AND project=?", (run, project)
    ).fetchone()
    if not job:
        raise ValueError("No such crawl in this project.")
    if job["state"] != "running":
        return status(db, project, run)
    db.execute(
        """UPDATE crawl_attempts SET finished=CURRENT_TIMESTAMP,outcome='interrupted'
        WHERE finished IS NULL AND lease IN (SELECT lease FROM crawl_pages WHERE crawl=? AND state='reading' AND expires<?)""",
        (run, time.time()),
    )
    db.execute(
        "UPDATE crawl_pages SET state='queued',lease=NULL WHERE crawl=? AND state='reading' AND expires<?",
        (run, time.time()),
    )
    rows = db.execute(
        "SELECT state,count(*) FROM crawl_pages WHERE crawl=? GROUP BY state", (run,)
    ).fetchall()
    counts = dict(rows)
    if counts.get("reading"):
        return status(db, project, run)
    count = sum(c for s, c in counts.items() if s not in ("reading", "queued"))
    if (
        count >= job["pages"]
        or job["spent"] >= job["seconds"]
        or not counts.get("queued")
    ):
        db.execute(
            "UPDATE crawls SET state=? WHERE id=?",
            ("budget reached" if counts.get("queued") else "complete", run),
        )
        return status(db, project, run)
    clock = db.execute(
        "SELECT next_fetch FROM crawl_clock WHERE crawl=?", (run,)
    ).fetchone()
    if clock and clock[0] > time.time():
        return status(db, project, run)
    page = trail.queue(db, run, trail.question(db, run, job["question"]))[0][0]
    lease = uuid.uuid4().hex
    db.execute(
        "UPDATE crawl_pages SET state='reading',lease=?,expires=? WHERE id=?",
        (lease, time.time() + 35, page["id"]),
    )
    db.execute(
        "INSERT INTO crawl_attempts(crawl,url,lease) VALUES(?,?,?)",
        (run, page["url"], lease),
    )
    db.commit()
    start = time.monotonic()
    deadline = start + min(18, job["seconds"] - job["spent"])
    host = urlsplit(job["seed"]).netloc
    robots = job["robots"]
    final = page["url"]
    body = title = detail = ""
    links = []
    result = "saved"
    try:
        if robots is None:
            code, _, _, data = fetch(
                urljoin(job["seed"], "/robots.txt"), host, deadline
            )
            if code == 200:
                robots = data
            elif code in (404, 410):
                robots = ""
            elif code in (401, 403):
                robots = "User-agent: *\nDisallow: /"
            else:
                raise ValueError(f"Could not check robots.txt (HTTP {code}).")
        rules = robotparser.RobotFileParser()
        rules.parse(robots.splitlines())
        if not rules.can_fetch(AGENT, final):
            raise ValueError("Excluded by robots.txt.")
        code, final, kind, raw = fetch(final, host, deadline, rules)
        if code != 200:
            raise ValueError(f"HTTP {code}")
        if kind in ("text/html", "application/xhtml+xml"):
            parsed = Page(final)
            parsed.feed(raw)
            body = parsed.text()
            title = "".join(parsed.titles).strip()
            links = parsed.links
        else:
            body = raw
        if not body.strip():
            raise ValueError("No extractable page text.")
        terms = set(re.findall(r"[a-z0-9]{3,}", job["question"].lower()))

        def overlap(line):
            return len(terms.intersection(re.findall(r"[a-z0-9]+", line.lower())))

        detail = max(body.splitlines(), key=overlap, default="")[:450]
        links.sort(key=overlap, reverse=True)
    except (OSError, ValueError, LookupError, urllib3.exceptions.HTTPError) as exc:
        result = "failed"
        detail = str(exc)[:450]
    db.execute("BEGIN IMMEDIATE")
    if not db.execute(
        "SELECT 1 FROM crawl_pages WHERE id=? AND lease=?", (page["id"], lease)
    ).fetchone():
        return status(db, project, run)
    source = edition = digest = None
    if result == "saved":
        receipt = capture(
            db,
            project,
            dict(title=(title or final)[:240], text=body, kind="web", origin=final),
        )
        source, edition, digest = (
            receipt["source"],
            receipt["edition"],
            receipt["digest"],
        )
        if db.execute(
            "SELECT 1 FROM crawl_pages WHERE crawl=? AND digest=?", (run, digest)
        ).fetchone():
            result = "duplicate"
        if page["depth"] < job["depth"]:
            room = (
                250
                - db.execute(
                    "SELECT count(*) FROM crawl_pages WHERE crawl=?", (run,)
                ).fetchone()[0]
            )
            for href in links:
                if room <= 0:
                    break
                try:
                    url = url_key(urljoin(final, href))
                except ValueError:
                    continue
                if (
                    urlsplit(url).netloc == host
                    and url != final
                    and rules.can_fetch(AGENT, url)
                ):
                    db.execute(
                        "INSERT OR IGNORE INTO crawl_edges(crawl,parent,child) VALUES(?,?,?)",
                        (run, final, url),
                    )
                    room -= db.execute(
                        "INSERT OR IGNORE INTO crawl_pages(crawl,url,depth) VALUES(?,?,?)",
                        (run, url, page["depth"] + 1),
                    ).rowcount
    db.execute(
        "UPDATE crawl_pages SET state=?,final_url=?,source=?,edition=?,digest=?,detail=?,lease=NULL WHERE id=?",
        (result, final, source, edition, digest, detail, page["id"]),
    )
    db.execute(
        "UPDATE crawl_attempts SET finished=CURRENT_TIMESTAMP,outcome=? WHERE lease=?",
        (result, lease),
    )
    db.execute("UPDATE crawls SET robots=COALESCE(robots,?) WHERE id=?", (robots, run))
    charge_elapsed(db, run)
    if robots is not None:
        rate = rules.request_rate(AGENT)
        delay = max(
            rules.crawl_delay(AGENT) or 0,
            rate.seconds / rate.requests if rate and rate.requests else 0,
        )
        db.execute(
            "UPDATE crawl_clock SET next_fetch=? WHERE crawl=?",
            (time.time() + delay, run),
        )
    return status(db, project, run)


def operate(db, project, args):
    db.executescript(DDL + trail.DDL)
    action = args["action"]
    run = args.get("crawl")
    if action == "crawl_trail":
        return trail.summarize(db, project, run)
    if action == "crawl_checkpoint":
        return trail.checkpoint(db, project, args)
    if action == "crawl_list":
        return {
            "crawls": [
                dict(r)
                for r in db.execute(
                    "SELECT id,question,state FROM crawls WHERE project=? ORDER BY id DESC LIMIT 50",
                    (project,),
                )
            ]
        }
    if action == "crawl_create":
        seed = url_key(field(args, "url", 4096))
        question = field(args, "question", 500)
        run = db.execute(
            "INSERT INTO crawls(project,question,seed,pages,depth,seconds) VALUES(?,?,?,?,?,?)",
            (
                project,
                question,
                seed,
                bounded(args, "pages", 5, 40),
                bounded(args, "depth", 1, 3, 0),
                bounded(args, "seconds", 30, 300),
            ),
        ).lastrowid
        db.execute(
            "INSERT INTO crawl_pages(crawl,url,depth) VALUES(?,?,0)", (run, seed)
        )
    elif action in ("crawl_pause", "crawl_resume"):
        db.execute("BEGIN IMMEDIATE")
        job = status(db, project, run)
        desired = "paused" if action == "crawl_pause" else "running"
        if desired == "running" and any(
            not c["verified"] for c in trail.summarize(db, project, run)["checked"]
        ):
            raise ValueError(
                "A previously saved source no longer matches its receipt. Review it before resuming."
            )
        if job["state"] in ("paused", "running") and (
            job["state"] != desired or desired == "running"
        ):
            # A new manual driver may be reopening a previously interrupted app.
            # Resume starts a fresh active interval; already completed requests were charged on commit.
            if desired == "paused":
                charge_elapsed(db, run)
            elif db.execute(
                "SELECT 1 FROM crawl_pages WHERE crawl=? AND state='reading' AND expires>?",
                (run, time.time()),
            ).fetchone():
                charge_elapsed(db, run)
            db.execute(
                "INSERT INTO crawl_clock(crawl,started) VALUES(?,?) ON CONFLICT(crawl) DO UPDATE SET started=excluded.started",
                (run, time.time() if desired == "running" else None),
            )
            db.execute("UPDATE crawls SET state=? WHERE id=?", (desired, run))
    elif action == "crawl_step":
        return step(db, project, run)
    elif action != "crawl_status":
        raise ValueError("Unknown crawl operation.")
    return status(db, project, run)
