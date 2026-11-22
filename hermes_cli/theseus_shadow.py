"""Local, opt-in decision observations. Workers never consume the recommendation."""
import argparse
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import subprocess

from hermes_constants import get_hermes_home

LOG = logging.getLogger(__name__)
KINDS = ("source", "resume", "save", "reviewer")
SCHEMA = """
CREATE TABLE IF NOT EXISTS watches(kind TEXT, scope TEXT, PRIMARY KEY(kind,scope));
CREATE TABLE IF NOT EXISTS decisions(
 id INTEGER PRIMARY KEY, kind TEXT, scope TEXT, event TEXT, recorded TEXT DEFAULT CURRENT_TIMESTAMP,
 context TEXT NOT NULL, alternatives TEXT NOT NULL, recommendation TEXT NOT NULL, criterion TEXT NOT NULL,
 UNIQUE(kind,scope,event));
CREATE TABLE IF NOT EXISTS outcomes(id INTEGER PRIMARY KEY REFERENCES decisions(id),
 observed TEXT DEFAULT CURRENT_TIMESTAMP, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER REFERENCES decisions(id), receipt TEXT, digest TEXT,
 verdict TEXT, PRIMARY KEY(id,receipt));
"""


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def policy(kind, state):
    if kind == "reviewer":
        options = ["inspect the most confident wrong prediction against its actual trace",
                   "seek an independent observation before relying on the reviewer",
                   "repeat on an unseen investigation before changing keep policy"]
        if state.get("isolation","verified") != "verified":
            options.append("repeat the blinded forecast in a fresh chat with verified context isolation")
            choice = options[-1]
        else:
            choice = options[0] if state["confident_misses"] else options[1] if state["brier"] >= .25 else options[2]
        return options, choice, "Use actual trace labels for the verdict; reviewer confidence only selects follow-up evidence."
    if kind == "source":
        options = ["answer from the matched observation", "report the gap and request the missing record"]
        choice = options[int(not state["direct_candidate"])]
        if state["unshown_comparisons"]:
            options.append("surface the checked comparison records with their scope, then answer or abstain")
            choice = options[-1]
        return options, choice, "Check direct claims, requested nearby results and the current decision against the sources."
    if kind == "resume":
        options = ["reuse the paused draft", "render with the governing claims and verify the saved bytes"]
        return options, options[int(state["draft_claims"] != state["current_claims"] or state.get("expected_sha256") != state.get("draft_sha256"))], "Reopen the output; its bytes must match the current rendering."
    options = ["retry the save", "read back persisted state before deciding whether to retry"]
    return options, options[1], "Reload after the lost acknowledgement; verify target, untouched sibling and exactly one save."


def observe(home, kind, scope, callback):
    path = Path(home) / "shadow" / "history.sqlite"
    if not path.is_file():
        return
    try:
        with closing(sqlite3.connect(path, timeout=0.25)) as db, db:
            if db.execute("SELECT 1 FROM watches WHERE kind=? AND scope=?", (kind, scope)).fetchone():
                callback(db)
    except Exception:
        LOG.warning("Theseus shadow observation unavailable", exc_info=False)


def propose(home, kind, scope, event, make_state):
    def append(db):
        state = make_state()
        options, choice, criterion = policy(kind, state)
        db.execute("""INSERT INTO decisions(kind,scope,event,context,alternatives,recommendation,criterion)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(kind,scope,event) DO NOTHING""",
            (kind, scope, event, json.dumps(state), json.dumps(options), choice, criterion))
    observe(home, kind, scope, append)


def outcome(home, kind, scope, event, readback):
    def append(db):
        row = db.execute("SELECT id FROM decisions WHERE kind=? AND scope=? AND event=?",
                         (kind, scope, event)).fetchone()
        if row and not db.execute("SELECT 1 FROM outcomes WHERE id=?", row).fetchone():
            db.execute("INSERT OR IGNORE INTO outcomes(id,evidence) VALUES(?,?)",
                       (row[0], json.dumps(readback())))
    observe(home, kind, scope, append)


def main():
    p = argparse.ArgumentParser()
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("watch", "unwatch"):
        sub = commands.add_parser(name)
        sub.add_argument("kind", choices=KINDS)
        sub.add_argument("scope", help="investigation project, project/job, or crash trial name")
    commands.add_parser("history")
    attach = commands.add_parser("attach", help="bind a Theseus proof receipt to an observed artifact")
    attach.add_argument("id", type=int)
    attach.add_argument("receipt", type=Path)
    args = p.parse_args()
    root = Path(get_hermes_home()) / "shadow"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(root / "history.sqlite")) as db, db:
        db.executescript(SCHEMA)
        if args.command == "watch":
            db.execute("INSERT OR IGNORE INTO watches VALUES(?,?)", (args.kind, args.scope))
        elif args.command == "unwatch":
            db.execute("DELETE FROM watches WHERE kind=? AND scope=?", (args.kind, args.scope))
        elif args.command == "attach":
            receipt = args.receipt.resolve(strict=True)
            if receipt.parent != (root.parent / "proofs").resolve():
                p.error("select a receipt in this profile's proofs directory")
            before = receipt.read_bytes()
            proof = json.loads(before)
            launcher = Path(__file__).resolve().parents[1] / "theseus"
            result = subprocess.run([str(launcher), "proof", "check", str(receipt)], capture_output=True, text=True)
            if result.returncode or before != receipt.read_bytes():
                p.error("proof is stale or inconsistent")
            row = db.execute("""SELECT kind,context,evidence FROM decisions
                                JOIN outcomes USING(id) WHERE id=?""", (args.id,)).fetchone()
            if not row:
                p.error("no observed outcome for this decision")
            kind, context, evidence = row[0], json.loads(row[1]), json.loads(row[2])
            if kind == "reviewer":
                p.error("reviewer forecasts are scored against their registered trace outcomes in the learning journal")
            if kind == "save":
                export = json.loads(Path(proof["artifact"]).read_text())
                valid = (proof.get("scope") == "persisted Desktop trial observations"
                         and Path(evidence["database"]).stem == export.get("trial")
                         and any(all(result.get(k) == v for k, v in evidence["readback"].items())
                                 for result in export.get("results", [])))
            else:
                valid = evidence["sha256"] == proof["artifact_sha256"]
                if kind == "source":
                    valid = valid and context["request_sha256"] == proof["request_sha256"]
                elif kind == "resume":
                    valid = valid and Path(evidence["path"]).resolve() == Path(proof["artifact"]).resolve()
            if not valid:
                p.error("receipt does not review this decision's observed artifact")
            db.execute("INSERT OR IGNORE INTO reviews VALUES(?,?,?,?)",
                       (args.id, str(receipt), hashlib.sha256(before).hexdigest(), proof["verdict"]))
        else:
            db.row_factory = sqlite3.Row
            for row in db.execute("SELECT d.*, o.observed,o.evidence FROM decisions d LEFT JOIN outcomes o USING(id) ORDER BY id"):
                item = dict(row)
                for key in ("context", "alternatives", "evidence"):
                    if item[key] is not None:
                        item[key] = json.loads(item[key])
                item["reviews"] = [dict(r) for r in db.execute("SELECT receipt,digest,verdict FROM reviews WHERE id=?", (item["id"],))]
                print(json.dumps(item))


if __name__ == "__main__":
    main()
