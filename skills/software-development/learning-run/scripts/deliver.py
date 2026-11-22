"""Deliver a bounded local artifact once and reconcile interruptions against its saved bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys

# The standalone script may run under a clean interpreter outside the checkout.
# Resolve its own backend before loading the same guarded artifact reader as the app.
if __name__ == "__main__":
    backend = Path(__file__).resolve().parent.joinpath("../../../..").resolve()
    if (backend / "hermes_cli" / "artifact_inputs.py").is_file():
        sys.path.insert(0, str(backend))

from hermes_cli.artifact_inputs import open_artifact, reject_auth_store

LIMIT = 16 * 1024 * 1024


def read_regular(path):
    with open_artifact(path, follow_links=False) as stream:
        data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("this delivery trial supports files up to 16 MiB")
        return data


def digest(data):
    return hashlib.sha256(data).hexdigest()


def admit(db, key, source, destination):
    # Resolve the parent only: a symlink at the leaf must never redirect publication.
    destination = destination.parent.resolve(strict=True) / destination.name
    source = source.parent.resolve(strict=True) / source.name
    reject_auth_store(destination)
    with db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM deliveries WHERE key=?", (key,)).fetchone()
        if old and (old["source"] != str(source) or old["destination"] != str(destination)):
            raise ValueError("job key already belongs to a different delivery")
        data = read_regular(source)
        if old:
            if old["sha"] != digest(data):
                raise ValueError("source changed since admission; resume the original job or use a new key")
            return
        stage = destination.parent / (".theseus-delivery-" + secrets.token_hex(16) + ".part")
        db.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,?,0)",
                   (key, str(source), str(destination), digest(data), data, str(stage)))


def observed_digest(path):
    try:
        return digest(read_regular(path))
    except FileNotFoundError:
        return None


def inspect_delivery(database, key=None):
    if not database.exists():
        if key is None:
            return []
        raise ValueError("unknown delivery key")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if key is None:
            return [{"job": row["key"], "path": row["destination"], "acknowledged": bool(row["done"])}
                    for row in db.execute("SELECT key,destination,done FROM deliveries ORDER BY done,rowid DESC")]
        row = db.execute("SELECT destination,sha,done FROM deliveries WHERE key=?", (key,)).fetchone()
        if not row:
            raise ValueError("unknown delivery key")
        path = Path(row["destination"])
        reject_auth_store(path)
        observed = observed_digest(path)
        state = "matching" if observed == row["sha"] else "missing" if observed is None else "conflict"
        return {"job": key, "path": str(path), "acknowledged": bool(row["done"]), "destination": state,
                "expected_sha256": row["sha"], "observed_sha256": observed}


def reconcile(db, key):
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM deliveries WHERE key=?", (key,)).fetchone()
        if not row:
            raise ValueError("unknown delivery key")
        dest, stage = Path(row["destination"]), Path(row["stage"])
        reject_auth_store(row["source"])
        reject_auth_store(dest)
        actual = observed_digest(dest)
        if actual is None and not row["done"]:
            # The random same-directory stage name was journaled before any filesystem action.
            stage.unlink(missing_ok=True)
            with stage.open("xb") as out:
                out.write(row["data"])
                out.flush()
                os.fsync(out.fileno())
            try:
                os.link(stage, dest)
            except FileExistsError:
                pass  # Another writer won. Read its actual output before acknowledging.
            actual = digest(read_regular(dest))
        stage.unlink(missing_ok=True)
        if actual != row["sha"]:
            return {"job": key, "path": str(dest), "state": "missing" if actual is None else "conflict",
                    "expected_sha256": row["sha"], "observed_sha256": actual}
        # Commit the directory entry before recording successful publication.
        fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        db.execute("UPDATE deliveries SET done=1 WHERE key=?", (key,))
        return {"job": key, "path": str(dest), "state": "verified", "sha256": actual}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    send = commands.add_parser("file")
    send.add_argument("key")
    send.add_argument("source", type=Path)
    send.add_argument("destination", type=Path)
    commands.add_parser("resume").add_argument("key")
    commands.add_parser("list", help="list recorded jobs, unacknowledged first")
    commands.add_parser("inspect", help="compare the destination with the admitted bytes without retrying").add_argument("key")
    args = parser.parse_args()
    if args.action != "list" and (not args.key.strip() or len(args.key) > 160):
        parser.error("choose a short, nonempty delivery key")
    os.umask(0o077)
    root = Path(os.environ["HERMES_HOME"]) / "deliveries"
    if args.action in ("list", "inspect"):
        print(json.dumps(inspect_delivery(root.absolute() / "jobs.sqlite", getattr(args, "key", None))))
        return 0
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with sqlite3.connect(root / "jobs.sqlite", timeout=20) as db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS deliveries(key TEXT PRIMARY KEY, source TEXT, destination TEXT,
                   sha TEXT, data BLOB, stage TEXT, done INTEGER)""")
        if args.action == "file":
            admit(db, args.key, args.source, args.destination)
        receipt = reconcile(db, args.key)
        print(json.dumps(receipt))
        return 0 if receipt["state"] == "verified" else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, sqlite3.Error) as exc:
        sys.exit(str(exc))
