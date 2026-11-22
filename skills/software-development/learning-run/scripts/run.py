"""Local experiment journal. Each phase is inserted exactly once per run."""
import argparse
from datetime import datetime, timezone, UTC
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile


NEXT_STEP = {"baseline": "propose", "proposal": "evaluate", "staging": "evaluate",
             "evaluation": "human review", "review": "complete"}


def correction_links(db, runs):
    # Read-only inspection also supports journals created before corrections existed.
    links = {}
    if runs and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='patch_corrections'").fetchone():
        for run, ref in db.execute("""SELECT run,reference FROM patch_corrections
                WHERE run IN (SELECT value FROM json_each(?)) ORDER BY run,reference""", (json.dumps(runs),)):
            links.setdefault(str(run), []).append(ref)
    return links


def mark_corrected_evidence(rows, links):
    for row in rows:
        refs = links.get(str(row["run"]))
        if refs and row["phase"] in ("staging", "evaluation"):
            row["patch_correction_runs"] = refs
    return rows


def inspect_job(key):
    database = Path(os.environ["HERMES_HOME"]).absolute() / "learning-runs" / "history.sqlite"
    if not database.exists():
        raise ValueError("unknown job key")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        row = db.execute("""SELECT j.key AS job, j.run, latest.problem, latest.phase,
            json_extract(tested.details,'$.recommendation') AS recommendation,
            json_extract(reviewed.details,'$.decision') AS decision,
            (SELECT r.run FROM events r WHERE r.phase='review'
             AND json_extract(r.details,'$.supersedes')=j.run) AS superseded_by
            FROM jobs j JOIN events latest ON latest.run=j.run
            LEFT JOIN events tested ON tested.run=j.run AND tested.phase='evaluation'
            LEFT JOIN events reviewed ON reviewed.run=j.run AND reviewed.phase='review'
            WHERE j.key=? ORDER BY latest.rowid DESC LIMIT 1""", (key,)).fetchone()
        if row is None:
            raise ValueError("unknown job key")
        refs = correction_links(db, [row["run"]]).get(str(row["run"]), [])
    return dict(row) | {"next": NEXT_STEP[row["phase"]], "patch_correction_runs": refs}


def journal():
    root = Path(os.environ["HERMES_HOME"]) / "learning-runs"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(root / "history.sqlite")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        run INTEGER, problem TEXT NOT NULL, phase TEXT NOT NULL,
        recorded TEXT NOT NULL, details TEXT NOT NULL, PRIMARY KEY(run, phase))""")
    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
        key TEXT PRIMARY KEY, run INTEGER NOT NULL UNIQUE)""")
    db.execute("CREATE TABLE IF NOT EXISTS patch_corrections(run INTEGER, reference INTEGER, PRIMARY KEY(run,reference))")
    db.execute("CREATE TABLE IF NOT EXISTS observations(run INTEGER,case_key TEXT,details TEXT NOT NULL,PRIMARY KEY(run,case_key))")
    db.execute("CREATE TABLE IF NOT EXISTS transfer_plans(run INTEGER,case_key TEXT,policy TEXT NOT NULL,PRIMARY KEY(run,case_key))")
    from reviewer_trials import SCHEMA as reviewer_schema
    db.executescript(reviewer_schema)
    return root, db


def problem_key(name):
    return re.sub(r"(?<=[^\W_])[-_\s]+(?=[^\W_])", " ", name.strip().casefold())


def history(db, problem):
    return [dict(row) | {"details": json.loads(row["details"])} for row in
            db.execute("SELECT * FROM events WHERE problem=? ORDER BY run, rowid", (problem,))]


def evidence_at_start(db, run):
    baseline = db.execute("SELECT rowid,problem,details FROM events WHERE run=? AND phase='baseline'", (run,)).fetchone()
    if not baseline:
        raise ValueError("unknown learning run")
    details = json.loads(baseline["details"])
    retrieved = details["retrieved runs"]
    rows = db.execute("""SELECT * FROM events WHERE rowid<? AND run IN (SELECT value FROM json_each(?))
                      ORDER BY (problem!=?),run,rowid""",
                      (baseline["rowid"], json.dumps(retrieved), baseline["problem"]))
    events = [dict(row) | {"details": json.loads(row["details"])} for row in rows]
    return mark_corrected_evidence(events, details.get("known patch corrections", {}))


def print_evidence(rows, brief):
    if brief:
        compact = []
        for row in rows:
            details = row["details"].copy()
            if "diff" in details:
                patch = details.pop("diff").encode("utf-8")
                details["patch reference"] = {
                    "bytes": len(patch), "sha256": hashlib.sha256(patch).hexdigest()
                }
            compact.append({**row, "details": details})
        rows = compact
    print(json.dumps(rows, indent=2, ensure_ascii=False))


def replacement(db, run):
    row = db.execute("""SELECT run FROM events WHERE phase='review'
                      AND json_extract(details,'$.supersedes')=?""", (run,)).fetchone()
    return row[0] if row else None


def render(db, rows, run, newer=None):
    phases = {row["phase"]: row["details"] for row in rows if row["run"] == run}
    baseline = phases["baseline"]
    proposal = phases.get("proposal", {})
    evaluation = phases.get("evaluation", {})
    review = phases.get("review", {})
    state = review.get("decision") or ("Awaiting human review" if evaluation else "In progress")
    title = f"Learning run {run}"

    def field(label, value):
        if not value:
            return ""
        return f"<section><h2>{label}</h2><p>{html.escape(str(value))}</p></section>"

    fields = [
        field("Human decision", review.get("decision")),
        field("Review reason", review.get("reason")),
        field("Baseline", baseline.get("baseline")),
        field("Candidate result", evaluation.get("candidate")),
        field("Fresh tests", evaluation.get("fresh")),
        field("Recommendation", evaluation.get("recommendation")),
        field("Proposed change", proposal.get("change")),
        field("Rationale", proposal.get("rationale")),
        field("Expected result", proposal.get("expected")),
    ]
    for row in db.execute("SELECT details FROM observations WHERE run=? ORDER BY rowid", (run,)):
        observed = json.loads(row[0])
        if observed.get("trace"):
            trace = observed["trace"]
            fields.append(field("Transfer tool trace", observed["case"] + " — " + trace["status"] + ": " + trace["reason"]))
        fields.append(field("Why this next test?", observed["case"] + "\n" + observed["next_test"] +
                            "\n" + observed["why"] + "\n" + "\n".join(observed["gaps"])))
    for row in db.execute("SELECT s.key,s.model,s.result,a.isolation FROM reviewer_scores s LEFT JOIN reviewer_audits a ON a.session=s.session WHERE s.run=? ORDER BY s.rowid",(run,)):
        score = json.loads(row["result"])
        if row["isolation"] != "verified":
            fields.append(field("Excluded reviewer trial",f"{row['key']} · {row['model']} · " + (row["isolation"] or "isolation not audited")))
            continue
        fields.append(field("Blinded reviewer trial", f"{row['key']} · {row['model']} · {score['n']} outcomes. "
                            f"Brier {score['brier']:.3f}; uncertain baseline 0.250. "
                            f"Confident misses: {score['confident_misses']}. " + score["next_check"]
                            + (f" · {score['inspect_case']}" if score.get("inspect_case") else "")))
    job = db.execute("SELECT key FROM jobs WHERE run=?", (run,)).fetchone()
    commands = []
    if job:
        commands.append("./theseus learning inspect " + shlex.quote(job[0]))
    if evaluation.get("files"):
        commands.append(f"./theseus learning check-files {run}")
    if not commands:
        commands.append(f"./theseus learning evidence {run} --brief")
    instructions = evaluation.get("try")
    steps = f"<p>{html.escape(instructions)}</p>" if instructions else ""
    fields.insert(5, "<section><h2>Try this run</h2>" + steps +
                  "<p>From the Theseus checkout</p><pre>" + html.escape("\n".join(commands)) + "</pre></section>")
    if evaluation.get("next"):
        fields.insert(6, field("Next experiment", evaluation["next"]))
    for (reference,) in db.execute("SELECT reference FROM patch_corrections WHERE run=? ORDER BY reference", (run,)):
        fields.insert(0, f'<section><h2>Patch evidence correction</h2><p>The original patch in this report is inaccurate. '
                         f'See <a href="run-{reference}.html">run {reference}</a> for the corrected source comparison.</p></section>')
    for label, other in (("Superseded by", newer), ("Supersedes", review.get("supersedes"))):
        if other is not None:
            fields.insert(0, f'<section><h2>{label}</h2><a href="run-{other}.html">Run {other}</a></section>')
    prior = baseline.get("retrieved runs", [])
    if prior:
        links = [f'<a href="run-{i}.html">Run {i}</a>' for i in prior if type(i) is int and i > 0]
        fields.append("<section><h2>Prior attempts</h2><p>" + ", ".join(links) + "</p></section>")
    if evaluation.get("files"):
        links = []
        for item in evaluation["files"]:
            href = html.escape(Path(item["path"]).as_uri(), quote=True)
            links.append(f'<li><a href="{href}">{html.escape(item["label"])}</a> '
                         f'({item["bytes"]:,} bytes)</li>')
        fields.append("<section><h2>Evidence files</h2><ul>" + "".join(links) + "</ul></section>")
    source = phases.get("staging", {}).get("source revisions")
    if source:
        fields.append(field("Source revisions", f"Base {source['base']}\nCandidate {source['candidate']}\n"
                            + "\n".join(source["paths"])))
    staged = phases.get("staging", {}).get("diff")
    patch = staged or evaluation.get("diff")
    if patch:
        fields.append("<details><summary>Proposed diff</summary><pre>" + html.escape(patch) + "</pre></details>")
    tested = evaluation.get("diff")
    if staged and tested and staged != tested:
        fields.append("<details><summary>Tested diff (changed after staging)</summary><pre>"
                      + html.escape(tested) + "</pre></details>")
    content = (f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{title}</title>"
               "<style>:root{color-scheme:light dark}body{font:16px/1.5 system-ui;max-width:52rem;margin:2rem auto;padding:0 1rem}"
               "section,details{border-top:1px solid #8886;padding:.65rem 0}h1{line-height:1.15}"
               "h2,summary{font-size:1rem;font-weight:650}h2,p{margin:.2rem 0}p,pre{white-space:pre-wrap;overflow-wrap:anywhere}"
               "pre{font:13px/1.45 monospace}summary{cursor:pointer}</style>"
               f"<h1>{title}</h1><p>{html.escape(rows[0]['problem'])} · {html.escape(state)}</p>" + "".join(fields) + "</html>")
    return content.encode("utf-8")


def publish_if_changed(path, expected):
    prefix = f".{path.name}."
    # A killed publisher may have left its private temporary file behind.
    for orphan in path.parent.glob(prefix + "*.part"):
        orphan.unlink(missing_ok=True)
    try:
        matches = path.stat().st_size == len(expected) and path.read_bytes() == expected
    except FileNotFoundError:
        matches = False
    if not matches:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=prefix, suffix=".part", delete=False) as output:
            temp = Path(output.name)
            try:
                output.write(expected)
                output.flush()
                os.fsync(output.fileno())
                output.close()
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
    return matches


def verify_report(root, db, run):
    # Serialize publications so an older renderer cannot overwrite a newer phase.
    with db:
        db.execute("BEGIN IMMEDIATE")
        rows = [dict(row) | {"details": json.loads(row["details"])} for row in
                db.execute("SELECT * FROM events WHERE run=? ORDER BY rowid", (run,))]
        expected = render(db, rows, run, replacement(db, run))
        # Repair the reverse link as well when replaying a review after an interruption.
        older = rows[-1]["details"].get("supersedes")
        if older:
            old_rows = [dict(row) | {"details": json.loads(row["details"])} for row in
                        db.execute("SELECT * FROM events WHERE run=? ORDER BY rowid", (older,))]
            publish_if_changed(root / f"run-{older}.html", render(db, old_rows, older, run))
        path = root / f"run-{run}.html"
        matches = publish_if_changed(path, expected)
        index = [dict(row) for row in db.execute("""
            SELECT e.run, e.problem, e.phase, (SELECT key FROM jobs WHERE run=e.run) AS job, json_extract(e.details,'$.decision') AS decision,
                   json_extract(e.details,'$.recommendation') AS recommendation,
                   (SELECT r.run FROM events r WHERE r.phase='review' AND json_extract(r.details,'$.supersedes')=e.run) AS superseded_by
            FROM events e JOIN (SELECT MAX(rowid) AS latest FROM events GROUP BY run) j
            ON e.rowid=j.latest ORDER BY e.run DESC""")]
        publish_if_changed(root / "index.json", json.dumps(index, ensure_ascii=False).encode("utf-8"))
        return {"run": run, "report": str(path), "report_state": "verified" if matches else "restored",
                "next": NEXT_STEP[rows[-1]["phase"]]}


def evaluation_files(first, last=None):
    database = Path(os.environ["HERMES_HOME"]).absolute() / "learning-runs" / "history.sqlite"
    if not database.exists():
        return {}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("""SELECT run, json_extract(details, '$.files') FROM events
                WHERE phase='evaluation' AND run>=? AND (? IS NULL OR run<=?) ORDER BY run""",
                          (first, last, last))
        return {run: json.loads(files) if files else [] for run, files in rows}


def verify_files(refs):
    results = []
    for ref in refs:
        try:
            observed = evidence_file(ref["label"], ref["path"])
            state = "current" if observed == ref else "changed"
        except FileNotFoundError:
            state = "missing"
        except (OSError, ValueError):
            state = "unverifiable"
        results.append({"label": ref["label"], "path": ref["path"], "state": state})
    return results


def audit_files(registry):
    counts = {}
    problems = []
    uncovered = []
    for run, refs in registry.items():
        if not refs:
            uncovered.append(run)
        for result in verify_files(refs):
            state = result["state"]
            counts[state] = counts.get(state, 0) + 1
            if state != "current":
                problems.append({"run": run, **result})
    return {"evaluations": len(registry), "registered": sum(counts.values()), "file_states": counts,
            "without_registered_files": uncovered, "problems": problems,
            "complete": bool(registry) and not uncovered and not problems}


def evidence_file(label, raw_path):
    from hermes_cli.artifact_inputs import open_artifact

    label = label.strip()
    if not 1 <= len(label) <= 100:
        raise ValueError("give each evidence file a label of up to 100 characters")
    path = Path(raw_path).expanduser().resolve(strict=True)
    profile = Path(os.environ["HERMES_HOME"]).resolve()
    if (path.is_relative_to(Path.home() / ".codex") or path.is_relative_to(Path.home() / ".hermes")
            or path == profile / "auth.json" or path.name.startswith(".env")
            or path.suffix.lower() not in {".txt", ".md", ".csv", ".json", ".jsonl", ".html", ".png", ".pdf", ".svg", ".xml"}):
        raise ValueError("select a local report, test output, or artifact")
    with open_artifact(path, follow_links=False) as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > 64 * 1024 * 1024:
            raise ValueError("select a regular evidence file of at most 64 MiB")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
                after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
            raise ValueError("evidence file changed while being read")
    return dict(label=label, path=str(path), bytes=before.st_size, sha256=digest)


def find_attempts(db, query):
    words = re.findall(r"[^\W_]+", query.casefold())[:24]
    if not words:
        raise ValueError("enter at least one search word")
    db.execute("""CREATE VIRTUAL TABLE temp.attempt_search USING fts5(
        problem,baseline,proposal,outcome,review,tokenize='porter unicode61')""")
    db.execute("""INSERT INTO attempt_search(rowid,problem,baseline,proposal,outcome,review)
        SELECT b.run,b.problem,json_extract(b.details,'$.baseline'),
               json_extract(p.details,'$.change') || ' ' || json_extract(p.details,'$.rationale'),
               json_extract(e.details,'$.candidate') || ' ' || json_extract(e.details,'$.fresh'),
               json_extract(r.details,'$.decision') || ' ' || json_extract(r.details,'$.reason')
        FROM events b JOIN events p ON b.run=p.run AND p.phase='proposal'
        JOIN events e ON b.run=e.run AND e.phase='evaluation'
        LEFT JOIN events r ON b.run=r.run AND r.phase='review' WHERE b.phase='baseline'""")
    matches = db.execute("""SELECT rowid,problem,snippet(attempt_search,-1,'[',']',' … ',24)
        FROM attempt_search WHERE attempt_search MATCH ?
        ORDER BY bm25(attempt_search,5,2,1,3,4),rowid DESC LIMIT 5""",
        (" OR ".join('"' + w + '"' for w in words),))
    for run, problem, excerpt in matches:
        phases = dict(db.execute("SELECT phase,details FROM events WHERE run=?", (run,)))
        review = json.loads(phases.get("review", "{}"))
        reason = review.get("reason")
        yield dict(run=run, problem=problem, excerpt=excerpt, superseded_by=replacement(db, run),
                   patch_correction_runs=correction_links(db, [run]).get(str(run), []),
                   recommendation=json.loads(phases["evaluation"])["recommendation"],
                   decision=review.get("decision"),
                   decision_reason=reason[:600] + "…" if reason and len(reason) > 600 else reason)


def revision_patch(revisions, paths):
    if not 1 <= len(paths) <= 16 or len(set(paths)) != len(paths):
        raise ValueError("select one to sixteen distinct repository files")
    for name in paths:
        path = Path(name)
        if (path.is_absolute() or str(path) != name or ".." in path.parts or ".git" in path.parts
                or path.name in ("auth.json", ".env") or path.name.startswith(".env.")
                or any(ord(c) < 32 for c in name)):
            raise ValueError("select explicit repository-relative source files")
    def git(*args):
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()
    root = git("rev-parse", "--show-toplevel")
    command = ["git", "-C", root, "--literal-pathspecs"]
    commits = [git("-C", root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
               for ref in revisions]
    found = set()
    for commit in commits:
        output = subprocess.check_output([*command, "ls-tree", "-z", commit, "--", *paths])
        for entry in output.split(b"\0"):
            if entry:
                metadata, name = entry.split(b"\t", 1)
                if metadata.split()[0] not in (b"100644", b"100755"):
                    raise ValueError("select regular files, not directories or links")
                found.add(os.fsdecode(name))
    if found != set(paths):
        raise ValueError("a selected path is absent from both revisions")
    with subprocess.Popen([*command, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
                           *commits, "--", *paths], stdout=subprocess.PIPE) as proc:
        data = proc.stdout.read(65537)
        if len(data) > 65536:
            proc.kill()
            raise ValueError("select a smaller source comparison")
        if proc.wait():
            raise ValueError("could not compare the selected revisions")
    return data.decode("utf-8"), {"base": commits[0], "candidate": commits[1], "paths": paths}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="cmd", required=True)
    observation = commands.add_parser("observe", help="choose a diagnostic from paired chat and artifact proof receipts")
    observation.add_argument("run", type=int)
    observation.add_argument("case", help="stable trial key")
    observation.add_argument("--chat", type=Path, required=True)
    observation.add_argument("--artifact", type=Path, required=True)
    observation.add_argument("--session", help="original Desktop chat session for a transfer")
    registration = commands.add_parser("trace-plan", help="freeze approved sources before a transfer chat")
    registration.add_argument("run", type=int)
    registration.add_argument("case")
    registration.add_argument("--investigation", nargs=2, metavar=("PROJECT","CONCEPT"))
    registration.add_argument("--claim", type=json.loads, help="measured field and scope required in the actual native lookup")
    registration.add_argument("--max-reads", type=int, help="maximum successful evidence reads in this bounded trial")
    registration.add_argument("--evidence", type=Path, action="append", default=[])
    registration.add_argument("--source-receipt", action="append",default=[],help="freeze an exact archived version attached to this investigation")
    packet = commands.add_parser("reviewer-trial", help="prepare a blinded packet from measured source traces")
    scoring = commands.add_parser("score-reviewer", help="score a fresh reviewer against observed outcomes")
    for cmd in (packet, scoring):
        cmd.add_argument("run", type=int)
        cmd.add_argument("key")
    packet.add_argument("--case", action="append", required=True)
    packet.add_argument("--mode", choices=("overall","specific"), required=True)
    scoring.add_argument("--session", required=True)
    start = commands.add_parser("begin")
    start.add_argument("problem", help="stable recurring problem name")
    start.add_argument("--baseline", required=True)
    start.add_argument("--job", help="stable request key to resume if interrupted")
    start.add_argument("--prior", action="append", type=int, default=[], help="retrieve an additional prior run")
    draft = commands.add_parser("draft", help="ask Theseus for a proposal from frozen prior attempts")
    draft.add_argument("run", type=int)
    draft.add_argument("--reuse", action="store_true", help="recover the same saved model turn on retry")
    commands.add_parser("find", help="search the evidence from evaluated attempts").add_argument("query")
    evidence = commands.add_parser("evidence", help="show the prior phases available when a run began")
    evidence.add_argument("run", type=int)
    for reader in (start, evidence):
        reader.add_argument("--brief", action="store_true", help="replace prior patch bodies with verified content identifiers")
    audit = commands.add_parser("audit", help="recheck registered files across evaluations and report coverage gaps")
    audit.add_argument("--from-run", type=int, default=1, help="first run number to include")
    commands.add_parser("check-files", help="verify the files cited at evaluation").add_argument("run", type=int)
    commands.add_parser("inspect", help="read a learning job by exact key without changing its report").add_argument("job")
    correction = commands.add_parser("flag-diff", help="link inaccurate patch evidence to a later correction report")
    correction.add_argument("run", type=int)
    correction.add_argument("--see", type=int, required=True)
    resume = commands.add_parser("resume")
    resume.add_argument("job")
    for cmd, fields in (("propose", ["change", "rationale", "expected"]),
                        ("stage", ["diff"]), ("evaluate", ["diff", "candidate", "fresh", "recommendation"]),
                        ("review", ["decision", "reason"]), ("show", [])):
        sub = commands.add_parser(cmd)
        sub.add_argument("run", type=int)
        if cmd == "evaluate":
            sub.add_argument("--try", default=argparse.SUPPRESS, help="manual steps for repeating the task")
            sub.add_argument("--next", default=argparse.SUPPRESS, help="the next experiment you recommend")
            sub.add_argument("--file", action="append", nargs=2, metavar=("LABEL", "PATH"), default=[])
        if cmd == "review":
            sub.add_argument("--supersedes", type=int, help="earlier reviewed run explicitly replaced by this decision")
        if cmd == "stage":
            source = sub.add_mutually_exclusive_group(required=True)
            source.add_argument("--diff")
            source.add_argument("--between", nargs=2, metavar=("BASE", "CANDIDATE"))
            sub.add_argument("--path", action="append", default=[], help="repository file to compare between commits")
            continue
        for field in fields:
            choices = ("keep", "revert") if field in ("decision", "recommendation") else None
            sub.add_argument("--" + field, required=(cmd, field) != ("evaluate", "diff"), choices=choices)
    args = parser.parse_args()
    os.umask(0o077)
    if args.cmd == "draft":
        from draft import draft_run

        path = Path(os.environ["HERMES_HOME"]).absolute() / "learning-runs" / "history.sqlite"
        if not path.is_file():
            parser.error("no learning journal in this profile")
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as reader:
                reader.row_factory = sqlite3.Row
                result = draft_run(reader, args.run, evidence_at_start, args.reuse)
        except (OSError, sqlite3.Error, ValueError, subprocess.TimeoutExpired) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.cmd == "inspect":
        try:
            print(json.dumps(inspect_job(args.job), ensure_ascii=False))
        except ValueError as exc:
            parser.error(str(exc))
        return
    if args.cmd in ("audit", "check-files"):
        if args.cmd == "audit":
            if args.from_run < 1:
                parser.error("--from-run must be positive")
            result = audit_files(evaluation_files(args.from_run))
            passed = result["complete"]
        else:
            registry = evaluation_files(args.run, args.run)
            if args.run not in registry:
                parser.error("no evaluation for this run")
            files = verify_files(registry[args.run])
            result = {"run": args.run, "files": files, "registered": len(files)}
            passed = bool(files) and all(item["state"] == "current" for item in files)
        print(json.dumps(result, ensure_ascii=False))
        if not passed:
            raise SystemExit(2)
        return
    root, db = journal()
    if args.cmd in ("reviewer-trial","score-reviewer"):
        from reviewer_trials import challenge, score
        try:
            if args.cmd == "reviewer-trial":
                print(challenge(root, db, args.run, args.key, args.case, args.mode))
            else:
                result = score(root,db,args.run,args.key,args.session)
                verify_report(root, db, args.run)
                print(json.dumps(result,ensure_ascii=False))
        except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
            parser.error(str(exc))
        return
    if args.cmd in ("trace-plan", "observe"):
        from observations import observe
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", args.case):
            parser.error("choose a short lowercase case key")
        try:
            if args.cmd == "trace-plan":
                from transfer_trace import plan
                result = plan(root, db, args.run, args.case, args.evidence, args.investigation, args.claim, args.max_reads,args.source_receipt)
            else:
                result = observe(root, db, args.run, args.case, args.chat, args.artifact, args.session)
        except (OSError, ValueError, KeyError) as exc:
            parser.error(str(exc))
        verify_report(root, db, args.run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if args.cmd == "flag-diff":
        with db:
            db.execute("BEGIN IMMEDIATE")
            evaluated = db.execute("SELECT count(*) FROM events WHERE run IN (?,?) AND phase='evaluation'",
                                   (args.run, args.see)).fetchone()[0]
            if args.see <= args.run or evaluated != 2:
                parser.error("choose an evaluated run and a later evaluated correction")
            db.execute("INSERT OR IGNORE INTO patch_corrections VALUES(?,?)", (args.run, args.see))
        print(verify_report(root, db, args.run)["report"])
        return
    if args.cmd == "evidence":
        try:
            print_evidence(evidence_at_start(db, args.run), args.brief)
        except ValueError as exc:
            parser.error(str(exc))
        return
    if args.cmd == "find":
        try:
            print(json.dumps(list(find_attempts(db, args.query)), ensure_ascii=False, indent=2))
        except ValueError as exc:
            parser.error(str(exc))
        return
    if args.cmd == "resume":
        entry = db.execute("SELECT run FROM jobs WHERE key=?", (args.job,)).fetchone()
        if entry is None:
            parser.error("unknown job key")
        print(json.dumps(verify_report(root, db, entry[0])))
        return
    if args.cmd == "show":
        if db.execute("SELECT 1 FROM events WHERE run=?", (args.run,)).fetchone() is None:
            parser.error("unknown learning run")
        print(verify_report(root, db, args.run)["report"])
        return
    with db:
        db.execute("BEGIN IMMEDIATE")
        if args.cmd == "begin":
            problem = args.problem.strip().casefold()
            if not problem:
                parser.error("supply a recurring problem name")
            extra = sorted(set(args.prior))
            if args.job is not None:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", args.job):
                    parser.error("use a job key of 1 to 96 letters, digits, periods, underscores or hyphens")
                previous = db.execute("""SELECT e.run,e.problem,e.details FROM jobs j JOIN events e
                    ON e.run=j.run WHERE j.key=? AND e.phase='baseline'""", (args.job,)).fetchone()
                if previous:
                    details = json.loads(previous["details"])
                    if problem_key(previous["problem"]) != problem_key(problem) or details["baseline"] != args.baseline or details.get("selected prior", []) != extra:
                        parser.error("job key already belongs to a different request")
                    db.commit()
                    prior = evidence_at_start(db, previous["run"])
                    print_evidence(prior, args.brief)
                    print(verify_report(root, db, previous["run"])["report"])
                    return
            key = problem_key(problem)
            known = {run for run, name in db.execute("SELECT run,problem FROM events WHERE phase='baseline'")
                     if problem_key(name) == key}
            for prior_run in set(extra) - known:
                if not db.execute("SELECT 1 FROM events WHERE run=? AND phase='evaluation'", (prior_run,)).fetchone():
                    parser.error("additional prior runs must have a recorded evaluation")
            retrieved = sorted(known.union(extra))
            links = correction_links(db, retrieved)
            run = db.execute("SELECT COALESCE(MAX(run),0)+1 FROM events").fetchone()[0]
            if args.job:
                db.execute("INSERT INTO jobs VALUES(?,?)", (args.job, run))
            phase = "baseline"
            details = {"baseline": args.baseline, "retrieved runs": retrieved}
            if links:
                details["known patch corrections"] = links
            if extra:
                details["selected prior"] = extra
        else:
            run = args.run
            row = db.execute("SELECT problem FROM events WHERE run=?", (run,)).fetchone()
            if row is None:
                parser.error("unknown learning run")
            problem = row[0]
            rows = history(db, problem)
            phases = [r["phase"] for r in rows if r["run"] == run]
            phase = {"propose": "proposal", "stage": "staging", "evaluate": "evaluation", "review": "review"}[args.cmd]
            allowed = {"proposal": {"baseline"}, "staging": {"proposal"},
                       "evaluation": {"proposal", "staging"}, "review": {"evaluation"}}
            if phases[-1] not in allowed[phase]:
                parser.error(f"run is at {phases[-1]}")
            details = {k: v for k, v in vars(args).items() if k not in ("cmd", "run")}
        if args.cmd == "stage":
            revisions = details.pop("between")
            paths = details.pop("path")
            if revisions:
                try:
                    details["diff"], details["source revisions"] = revision_patch(revisions, paths)
                except ValueError as exc:
                    parser.error(str(exc))
                except (OSError, subprocess.CalledProcessError):
                    parser.error("could not resolve or compare the selected local commits")
            elif paths:
                parser.error("--path requires --between")
        if args.cmd == "evaluate":
            if details["diff"] is None:
                if "staging" not in phases:
                    parser.error("supply --diff or stage a patch before evaluating")
                del details["diff"]
            requested = details.pop("file")
            if len(requested) > 8 or len({label.strip() for label, _ in requested}) != len(requested):
                parser.error("choose up to eight distinctly labeled evidence files")
            if requested:
                try:
                    details["files"] = [evidence_file(label, path) for label, path in requested]
                except (OSError, ValueError) as exc:
                    parser.error(str(exc))
        if args.cmd == "review":
            old = args.supersedes
            if old is None:
                details.pop("supersedes", None)
            elif not (0 < old < run and db.execute("SELECT 1 FROM events WHERE run=? AND phase='review'", (old,)).fetchone()):
                parser.error("choose an earlier run with a recorded human decision")
            elif replacement(db, old):
                parser.error("that decision was already superseded; inspect its replacement")
        if details.get("diff") == "-":
            details["diff"] = sys.stdin.read(65537)
        if phase == "staging" and not details["diff"].strip():
            parser.error("stage a nonempty patch")
        payload = json.dumps(details, ensure_ascii=False)
        if len(payload) > 65536 or re.search(r"eyJ[\w-]{12,}\.[\w-]{12,}\.|(?:sk-|Bearer\s+)[\w-]{15,}|(?:refresh_token|access_token)\s*[\"']?\s*[:=]", payload, re.IGNORECASE):
            parser.error("supply concise evidence free of credentials")
        db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                   (run, problem, phase, datetime.now(UTC).isoformat(), payload))
    if args.cmd == "begin":
        print_evidence(evidence_at_start(db, run), args.brief)
    print(verify_report(root, db, run)["report"])


if __name__ == "__main__":
    # A skill can be invoked with system Python or runpy during crash recovery.
    location = Path(__file__).resolve()
    sys.path.append(str(location.parent))
    if (location.parents[4] / "hermes_cli").is_dir():
        sys.path.append(str(location.parents[4]))
    main()
