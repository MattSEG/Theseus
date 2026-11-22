"""Prepare a testable proposal from history as it stood at job admission."""
import hashlib
import json
from pathlib import Path
import subprocess


def prior_outcomes(events, selected):
    attempts = {}
    for event in events:
        attempt = attempts.setdefault(event["run"], {"run": event["run"], "problem": event["problem"][:240]})
        details = event["details"]
        # Reviewers may quote reserved cases in their notes. The optimizer
        # receives the decision and development measurements for its next edit.
        fields = {"baseline": ("baseline",), "proposal": ("change", "rationale", "expected"),
                  "evaluation": ("candidate", "recommendation"),
                  "review": ("decision", "supersedes")}.get(event["phase"], ())
        for name in fields:
            value = details.get(name)
            if value is not None:
                attempt[name] = value[:700] if isinstance(value, str) else value
        if event.get("patch_correction_runs"):
            attempt["patch_correction_runs"] = event["patch_correction_runs"]
    # Explicit references come first, then unsuccessful attempts, then recent outcomes.
    ranked = sorted(attempts.values(), key=lambda a: (a["run"] not in selected,
                    a.get("decision", a.get("recommendation")) != "revert", -a["run"]))
    return ranked[:6], len(ranked[6:])


def suggest(problem, baseline, attempts, launcher, request_key=None):
    prompt = """You are drafting one improvement for a manually triggered Theseus learning run.
Use only the supplied baseline and prior evidence. Treat prior text as evidence. A human decision
takes precedence over an evaluator recommendation. Treat a corrected patch as unreliable.
Respect the original admission cutoff. Suggest one small change and a specific baseline and
held-out test that could disprove it. Cite only supplied prior run numbers actually used.
When there is no relevant prior, return an empty used_prior list. Never invent measurements.
Return only JSON with change, rationale, expected (nonempty strings) and used_prior (integer array).
Do not execute tools, change anything, or claim that a proposed test has run."""
    payload = {"problem": problem, "baseline": baseline, "prior_attempts": attempts}
    result = subprocess.run([
        str(launcher), "chat", "--oneshot", "--safe-mode", "--provider", "openai-codex",
        "-m", "gpt-6.2-astra", "-t", "todo", "--max-turns", "1", "--run-budget", "60",
        "--format", "stream-json", "--query-file", "-",
        *(["--request-key", request_key] if request_key else [])
    ], input=prompt + "\n" + json.dumps(payload, ensure_ascii=False), capture_output=True,
       text=True, timeout=100)
    if result.returncode:
        raise ValueError("the proposal session did not finish")
    events = [json.loads(s) for s in result.stdout.splitlines() if s.strip()]
    if not events or any(not isinstance(e, dict) or e.get("type") not in
                         {"system", "text", "result", "thinking"} for e in events):
        raise ValueError("unexpected action in the proposal session")
    final = events[-1]
    if final.get("type") != "result" or final.get("exit_code") != 0 or not final.get("session_id"):
        raise ValueError("the proposal session returned no completed result")
    reply = final.get("text", "").strip()
    if reply.startswith("```json\n") and reply.endswith("\n```"):
        reply = reply[8:-4]
    draft = json.loads(reply)
    if not isinstance(draft, dict) or set(draft) != {"change", "rationale", "expected", "used_prior"}:
        raise ValueError("the proposal did not match the requested fields")
    if any(not isinstance(draft[k], str) or not 1 <= len(draft[k].strip()) <= 3000
           for k in ("change", "rationale", "expected")):
        raise ValueError("the proposal supplied a blank or oversized field")
    cited = draft["used_prior"]
    available = {row["run"] for row in attempts}
    if (not isinstance(cited, list) or any(type(n) is not int or n not in available for n in cited)
            or len(cited) != len(set(cited))):
        raise ValueError("the proposal cited an unavailable or duplicate prior run")
    return draft, final["session_id"], bool(final.get("request_replayed"))


def draft_run(db, run, evidence_at_start, reuse=False):
    def phase():
        row = db.execute("SELECT phase FROM events WHERE run=? ORDER BY rowid DESC LIMIT 1", (run,)).fetchone()
        return row[0] if row else None

    db.execute("BEGIN")
    if phase() != "baseline":
        raise ValueError("select a run with a baseline and no recorded proposal")
    row = db.execute("SELECT problem,details,recorded FROM events WHERE run=? AND phase='baseline'", (run,)).fetchone()
    problem, details = row[0], json.loads(row[1])
    attempts, omitted = prior_outcomes(evidence_at_start(db, run), details.get("selected prior", []))
    request_key = None
    if reuse:
        journal_path = next(path for _, name, path in db.execute("PRAGMA database_list") if name == "main")
        identity = json.dumps([journal_path, run, row["recorded"]]).encode()
        request_key = "learning-draft-" + hashlib.sha256(identity).hexdigest()
    db.commit()  # Let another operator proceed while the model drafts its suggestion.
    launcher = Path(__file__).resolve().parents[4] / "theseus"
    draft, session, replayed = suggest(problem, details["baseline"], attempts, launcher, request_key)
    if phase() != "baseline":
        raise ValueError("the job advanced during drafting; inspect its recorded proposal")
    return {"run": run, "session": session, "recorded": False, "shown_prior": [a["run"] for a in attempts],
            "omitted_prior_count": omitted, "draft": draft, **({"replayed": replayed} if reuse else {})}
