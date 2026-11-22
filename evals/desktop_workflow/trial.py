"""Bounded, manual Desktop trial. Score SQLite state independently of the UI."""
try:
    from hermes_cli import theseus_shadow as shadow
except ModuleNotFoundError as exc:
    if exc.name != "hermes_cli":
        raise
    shadow = None  # The standalone script also runs outside the installed harness.

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import random
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

CASES = ("normal", "reordered", "popup", "slow", "partial")
EDITIONS = ("6.0", "5.0")


def case_spec(db, case):
    # Older trials always used 6.0 as the target and 5.0 as the preserved edition.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='specs'").fetchone():
        return (*EDITIONS, case == "reordered")
    return db.execute("SELECT target,other,reverse FROM specs WHERE scenario=?", (case,)).fetchone()


def create(path, seed=None, lose_ack=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb"):
        pass
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE lost_ack(scenario TEXT PRIMARY KEY, committed INTEGER DEFAULT 0, reloaded INTEGER DEFAULT 0);
            CREATE TABLE specs(scenario TEXT PRIMARY KEY, target TEXT, other TEXT, reverse INTEGER);
            CREATE TABLE cards(scenario TEXT, edition TEXT, ready INTEGER,
                               PRIMARY KEY(scenario, edition));
            CREATE TABLE actions(scenario TEXT, edition TEXT, duplicate INTEGER, at REAL);
        """)
        if lose_ack:
            db.execute("INSERT INTO lost_ack(scenario) VALUES('slow')")
        for case in CASES:
            target, other = EDITIONS
            reverse = case == "reordered"
            if seed is not None:
                rng = random.Random(f"{seed}/{case}")
                major = rng.randint(3, 18)
                minor = rng.randint(1, 8)
                editions = [f"{major}.{minor}", f"{major}.{minor + 1}"]
                rng.shuffle(editions)
                target, other = editions
                reverse = rng.choice((True, False))
            db.execute("INSERT INTO specs VALUES(?,?,?,?)", (case, target, other, reverse))
            db.executemany("INSERT INTO cards VALUES(?,?,?)",
                           ((case, target, case == "partial"), (case, other, False)))


def prepare_visits(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS lost_ack(scenario TEXT PRIMARY KEY, committed INTEGER DEFAULT 0, reloaded INTEGER DEFAULT 0)")
        db.execute("CREATE TABLE IF NOT EXISTS visits(scenario TEXT PRIMARY KEY)")


def visit(path, case):
    with sqlite3.connect(path) as db:
        db.execute("UPDATE lost_ack SET reloaded=1 WHERE scenario=? AND committed=1", (case,))
        db.execute("INSERT OR IGNORE INTO visits VALUES(?)", (case,))


def save(path, case, edition):
    with sqlite3.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT ready FROM cards WHERE scenario=? AND edition=?", (case, edition)).fetchone()
        if row is None:
            raise ValueError("unknown card")
        db.execute("UPDATE cards SET ready=1 WHERE scenario=? AND edition=?", (case, edition))
        db.execute("INSERT INTO actions VALUES(?,?,?,?)", (case, edition, row[0], time.time()))
        return bool(db.execute("UPDATE lost_ack SET committed=1,reloaded=0 WHERE scenario=?", (case,)).rowcount)


def score(path, cases=CASES, details=False):
    with sqlite3.connect(path) as db:
        db.execute("BEGIN")  # Read every case at the same committed point in time.
        result = []
        for case in cases:
            target, other, _ = case_spec(db, case)
            rows = dict(db.execute("SELECT edition,ready FROM cards WHERE scenario=?", (case,)))
            wrong, duplicates, total = db.execute(
                "SELECT coalesce(sum(edition!=?),0),coalesce(sum(duplicate),0),count(*) FROM actions WHERE scenario=?",
                (target, case)).fetchone()
            visited = bool(db.execute("SELECT 1 FROM visits WHERE scenario=?", (case,)).fetchone())
            final = rows.get(target) == 1 and rows.get(other) == 0
            fault = db.execute("SELECT committed,reloaded FROM lost_ack WHERE scenario=?", (case,)).fetchone()
            checked = fault is None or fault == (1, 1)
            result.append(dict(case=case, visited=visited, correct_final_state=final, wrong_actions=wrong,
                               duplicate_actions=duplicates, passed=visited and final and checked and not (wrong or duplicates)))
            if details:
                latest = db.execute("""SELECT edition,duplicate FROM actions WHERE scenario=?
                                       ORDER BY rowid DESC LIMIT 20""", (case,)).fetchall()
                omitted = total - len(latest)
                result[-1].update(
                    expected_ready={target: True, other: False}, saved_ready=rows,
                    earlier_saves_omitted=omitted,
                    saves=[{"number": i, "edition": edition, "wrong_target": edition != target,
                            "duplicate": bool(duplicate)}
                           for i, (edition, duplicate) in enumerate(reversed(latest), start=omitted + 1)]
                )
            if fault is not None:
                result[-1]["reloaded_after_commit"] = checked
        return result


def render(path, case):
    with sqlite3.connect(path) as db:
        target, other, reverse = case_spec(db, case)
        cards = dict(db.execute("SELECT edition,ready FROM cards WHERE scenario=?", (case,)))
    links = " ".join(f'<a href="/?case={c}" aria-current="{"page" if c == case else "false"}">{c}</a>'
                     for c in CASES)
    parts = []
    for edition in ((other, target) if reverse else (target, other)):
        label = "Ready for print" if cards[edition] else "Draft"
        parts.append(f'<section><h2>Edition {edition}</h2><p>{label}</p>'
                     f'<button data-edition="{edition}">Mark edition {edition} ready</button></section>')
    overlay = ('<dialog aria-label="Print queue notice"><p>The print queue is available.</p>'
               '<button onclick="this.parentNode.close()">Continue</button></dialog>'
               if case == "popup" else "")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Theseus print review trial</title>
    <meta name="viewport" content="width=device-width, initial-scale=1"><style>
    body{{font:16px system-ui;margin:2rem auto;max-width:48rem;padding:0 1rem;background:#fafafa;color:#181818}}
    nav{{display:flex;gap:1rem;flex-wrap:wrap}}a{{color:#33475b}}[aria-current=page]{{font-weight:bold}}
    section{{border-top:1px solid #888;padding:.75rem 0}}button{{padding:.55rem .9rem;font:inherit}}
    dialog{{border:1px solid #777}}dialog::backdrop{{background:#0005}}</style></head>
    <body><nav aria-label="Trial cases">{links}</nav><h1>Print review</h1>
    <p>Set edition {target} to Ready for print. Preserve edition {other} and avoid repeated saves.</p>
    {''.join(parts)}<p role="status"></p>{overlay}
    <script>document.querySelector('dialog')?.showModal();
    for (const button of document.querySelectorAll('[data-edition]')) {{
      button.onclick = async () => {{
        button.disabled = true;
        const status = document.querySelector('[role=status]');
        status.textContent = 'Saving…';
        try {{
          const response = await fetch('/save?case={case}', {{method:'POST',
            headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{edition:button.dataset.edition}})}});
          if (!response.ok) throw new Error('Save failed');
          button.parentNode.querySelector('p').textContent='Ready for print';
          status.textContent='Saved';
        }} catch {{ status.textContent='Save acknowledgement unavailable. Reload to check the saved state.' }}
        finally {{button.disabled=false}}
      }};
    }}</script></body></html>"""


def serve(path, minutes):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, content, mime):
            data = content.encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def route(self):
            parsed = urlsplit(self.path)
            case = parse_qs(parsed.query).get("case", ["normal"])[0]
            return parsed.path, case

        def do_GET(self):
            route, case = self.route()
            if self.headers.get("Host") != authority or route != "/" or case not in CASES:
                self.send_error(404)
                return
            visit(path, case)
            self.reply(200, render(path, case), "text/html; charset=utf-8")

        def do_POST(self):
            route, case = self.route()
            if (route != "/save" or case not in CASES or self.headers.get("Host") != authority
                    or self.headers.get("Origin") != "http://" + authority):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 256:
                    raise ValueError("bad request size")
                edition = json.loads(self.rfile.read(length))["edition"]
                if not isinstance(edition, str):
                    raise ValueError("unknown edition")
                if save(path, case, edition):
                    if shadow is not None:
                        shadow.propose(path.parent.parent, "save", path.stem, case,
                                       lambda: {"acknowledgement": "unavailable", "requested_edition": edition})
                    self.close_connection = True
                    return
            except (ValueError, KeyError, TypeError):
                self.send_error(400)
                return
            if case == "slow":
                time.sleep(3)
            self.reply(200, '{"saved":true}', "application/json")
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        authority = f"127.0.0.1:{server.server_port}"
        server.timeout = 0.5
        print(f"http://{authority}/", flush=True)
        end = time.monotonic() + minutes * 60
        while time.monotonic() < end:
            server.handle_request()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "serve", "score", "verify"))
    parser.add_argument("trial")
    parser.add_argument("--minutes", type=int, choices=range(1, 61), default=20, metavar="1..60")
    parser.add_argument("--seed", type=int, help="vary target edition and row order in a new trial")
    parser.add_argument("--lose-ack", action="store_true", help="drop the slow-case response after saving")
    parser.add_argument("--case", choices=CASES, action="append", help="case to certify; default is all cases")
    parser.add_argument("--details", action="store_true",
                        help="include the saved edition states and last 20 saves per verified case")
    args = parser.parse_args()
    if args.details and args.action != "verify":
        parser.error("--details applies only to verify")
    if args.case and args.action != "verify":
        parser.error("--case applies only to verify")
    if args.lose_ack and args.action != "start":
        parser.error("--lose-ack is recorded when creating the trial")
    if args.seed is not None and args.action != "start":
        parser.error("--seed selects cases only at trial creation")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.trial):
        parser.error("choose a short alphanumeric trial name")
    os.umask(0o077)
    path = Path(os.environ["HERMES_HOME"]) / "desktop-trials" / (args.trial + ".sqlite")
    if args.action == "start":
        if path.exists():
            parser.error("trial exists; serve to resume it")
        create(path, args.seed, args.lose_ack)
    if not path.is_file():
        parser.error("unknown trial")
    prepare_visits(path)
    if args.action == "verify":
        requested = list(dict.fromkeys(args.case or CASES))
        rows = score(path, requested, details=args.details)
        for row in rows:
            if row.get("reloaded_after_commit") or row["duplicate_actions"] or row["wrong_actions"]:
                if shadow is not None:
                    shadow.outcome(path.parent.parent, "save", path.stem, row["case"],
                                   lambda: {"database": str(path), "readback": row})
        passed = sum(row["passed"] for row in rows)
        print(json.dumps({"trial": args.trial, "requested": requested, "passed": passed,
                          "total": len(rows), "verified": passed == len(rows), "results": rows}, indent=2))
        return 0 if passed == len(rows) else 2
    if args.action == "score":
        print(json.dumps(score(path), indent=2))
    else:
        serve(path, args.minutes)


if __name__ == "__main__":
    raise SystemExit(main())
