"""Manually start a bounded delayed-retry test in its own disposable profile."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

JOURNAL = Path(__file__).resolve().parents[2] / "skills/software-development/learning-run/scripts/run.py"
FAULTS = ("unchanged", "missing report", "partial output", "later job", "corrupt index", "unchanged")


def write_json(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", delete=False) as out:
        json.dump(data, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(out.name, path)


def run_trial(root, until):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    frozen = root / "journal-under-test.py"
    if not frozen.exists():
        frozen.write_bytes(JOURNAL.read_bytes())
    build = hashlib.sha256(frozen.read_bytes()).hexdigest()
    # Each invocation runs in a fresh worker, with just the disposable profile.
    def worker(*args):
        return subprocess.check_output([sys.executable, str(frozen), *args],
                                       env={"HERMES_HOME": str(root / "worker")}, text=True)
    manifest = root / "manifest.json"
    if not manifest.exists():
        start = time.time()
        if not start < until <= start + 12 * 3600:
            raise ValueError("choose a future finish within twelve hours")
        report = Path(worker("begin", "print recovery", "--job", "original", "--baseline", "Edition 6.0 reviewed").splitlines()[-1])
        # A retry of setup may find the proposal already durable.
        if json.loads(worker("resume", "original"))["next"] == "propose":
            worker("propose", "1", "--change", "Retain print signoff", "--rationale", "Preserve review", "--expected", "One report")
        write_json(manifest, dict(start=start, finish=until, worker_sha256=build, report=str(report),
                                  sha256=hashlib.sha256(report.read_bytes()).hexdigest()))
    setup = json.loads(manifest.read_text())
    if abs(setup["finish"] - until) > 1:
        raise ValueError("resume with the original finish time")
    if setup.get("worker_sha256", build) != build:
        raise ValueError("the journal worker changed; preserve the original build")
    report = Path(setup["report"])
    for i, fault in enumerate(FAULTS):
        checkpoint = root / f"checkpoint-{i}.json"
        if checkpoint.exists():
            continue
        due = setup["start"] + (setup["finish"] - setup["start"]) * i / (len(FAULTS) - 1)
        while (remaining := due - time.time()) > 0:
            time.sleep(min(remaining, 30))
        if fault == "missing report":
            report.unlink(missing_ok=True)
        if fault == "partial output":
            (report.parent / ("." + report.name + ".fault.part")).write_text("incomplete")
            report.write_text("partial")
        if fault == "later job":
            worker("begin", "different review", "--job", "later", "--baseline", "Separate request")
        if fault == "corrupt index":
            (report.parent / "index.json").write_text("interrupted")
        receipt = json.loads(worker("resume", "original"))
        digest = hashlib.sha256(report.read_bytes()).hexdigest()
        with sqlite3.connect(report.parent / "history.sqlite") as db:
            phases = [r[0] for r in db.execute("SELECT phase FROM events WHERE run=1 ORDER BY rowid")]
        jobs = json.loads((report.parent / "index.json").read_text())
        passed = (receipt["run"] == 1 and receipt["next"] == "evaluate" and digest == setup["sha256"]
                  and phases == ["baseline", "proposal"] and sum(j["run"] == 1 for j in jobs) == 1
                  and not list(report.parent.glob(".run-1.html.*.part")))
        write_json(checkpoint, dict(fault=fault, checked=datetime.now(timezone.utc).isoformat(), passed=passed,
                                    sha256=digest, worker_sha256=build, phases=phases, receipt=receipt))
        if not passed:
            raise RuntimeError(f"failed checkpoint {i}: {fault}")
    results = [json.loads((root / f"checkpoint-{i}.json").read_text()) for i in range(len(FAULTS))]
    write_json(root / "result.json", {"passed": all(r["passed"] for r in results), "checkpoints": results})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--until", required=True, help="ISO 8601 finish with timezone")
    args = parser.parse_args()
    when = datetime.fromisoformat(args.until)
    if when.tzinfo is None or not args.name.isascii() or not args.name.replace("-", "").isalnum():
        parser.error("use a named local trial and a timezone-aware finish")
    os.umask(0o077)
    run_trial(Path.home() / ".theseus/trials/delayed-recovery" / args.name, when.timestamp())
