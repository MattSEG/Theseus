"""Choose the next diagnostic from two independent reviews of the same request."""
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import sqlite3

from hermes_cli.artifact_inputs import open_artifact


def reviewed(root, path):
    path = path.resolve(strict=True)
    if path.parent != (root.parent / "proofs").resolve():
        raise ValueError("choose a proof receipt from this profile")
    with open_artifact(path) as stream:
        raw = stream.read(200001)
    if len(raw) > 200000:
        raise ValueError("review is too large")
    proof = json.loads(raw)
    checker = Path(__file__).with_name("proof.py")
    check = subprocess.run([sys.executable, str(checker), "check", str(path)], capture_output=True, text=True)
    if check.returncode or path.read_bytes() != raw:
        raise ValueError("review changed or its supporting evidence is stale")
    return proof, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def next_test(chat, saved, previous):
    uncertain = [k for k in chat if "unverified" in (chat[k], saved[k])]
    file_gap = [k for k in chat if chat[k] == "pass" and saved[k] == "fail"]
    chat_gap = [k for k in chat if saved[k] == "pass" and chat[k] == "fail"]
    shared = [k for k in chat if chat[k] == saved[k] == "fail"]
    if uncertain:
        return "verify", uncertain, "Obtain a direct observation for the unverified criterion before diagnosing a defect.", "The reviews cannot distinguish missing proof from incorrect behavior."
    if file_gap:
        return "render", file_gap, "Compare the paused draft, post-correction preview and reopened file field by field.", "If the preview is already wrong, inspect claim binding or rendering; if the preview is right, inspect publication or retry."
    if chat_gap:
        return "context", chat_gap, "Compare the fresh turn's received context with its persisted answer.", "A missing claim suggests routing or retrieval; a visible claim used incorrectly suggests interpretation."
    if shared:
        return "ledger", shared, "Read the governing revision, scope and source, then trace them into both outputs.", "A shared error may originate in the ledger or in both consumers' interpretation of the same evidence."
    if previous and previous["choice"] == "transfer":
        return "stop", [], "Stop this bounded cycle and review the original and transfer results.", "Both outputs met the fixed criteria after an independent case change."
    if previous and previous["choice"] in {"render", "context", "ledger", "verify", "stress"}:
        return "transfer", [], "Freeze the candidate and repeat in an unseen project with different evidence and decisions.", "A repaired example can pass through case-specific wording; transfer tests the same acceptance criteria elsewhere."
    return "stress", [], "Change one boundary: introduce a second correction between pause and publication, then reopen the output.", "An ordinary pass cannot distinguish correct revision tracking from reuse of the last value seen."


def observe(root, db, run, case, chat_path, artifact_path, session=None):
    chat, c_ref = reviewed(root, chat_path)
    artifact, a_ref = reviewed(root, artifact_path)
    criteria = chat["acceptance_criteria"]
    if (not criteria or criteria != artifact["acceptance_criteria"] or
            chat["request_sha256"] != artifact["request_sha256"] or
            chat["session"] == artifact["session"] or chat["artifact"] == artifact["artifact"]):
        raise ValueError("use separate reviews of distinct outputs against the same request and explicit criteria")
    scores = []
    for proof in (chat, artifact):
        assessed = {c["requirement"]: c["status"] for c in proof["checks"]}
        if any(k not in assessed for k in criteria):
            raise ValueError("review omits an anchor criterion")
        row = {k: assessed[k] for k in criteria}
        other = [v for k, v in assessed.items() if k not in criteria]
        row["Additional request requirements"] = next((v for v in ("fail", "unverified") if v in other), "pass")
        scores.append(row)
    with db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM events WHERE run=?", (run,)).fetchone():
            raise ValueError("unknown run")
        prior = [json.loads(r[0]) for r in db.execute("SELECT details FROM observations WHERE run=? ORDER BY rowid",(run,))]
        refs = [c_ref, a_ref]
        policy = db.execute("SELECT policy FROM transfer_plans WHERE run=? AND case_key=?", (run, case)).fetchone()
        for record in prior:
            if record["case"] == case:
                if refs != record["proofs"] or session != (record.get("trace") or {}).get("session"):
                    raise ValueError("case already recorded with different proof; choose a new case key")
                old_trace = record.get("trace") or {}
                if old_trace.get("status") == "pass":
                    from transfer_trace import inspect_trace
                    checked = inspect_trace(root, json.loads(policy[0]) if policy else None, session, chat, artifact)
                    if checked != old_trace:
                        raise ValueError("the original transfer trace changed; collect a fresh case")
                return record
            if session and session == (record.get("trace") or {}).get("session"):
                raise ValueError("transfer session already used for another case")
        if prior and criteria != prior[0]["criteria"]:
            raise ValueError("anchor criteria changed within this run")
        previous = prior[-1] if prior else None
        choice, gaps, test, reason = next_test(*scores, previous)
        if choice == "stop" and artifact["request_sha256"] == previous["request_sha256"]:
            raise ValueError("transfer requires a different case request")
        if previous and choice == previous["choice"] and gaps == previous["gaps"] and gaps:
            choice, test, reason = "verify", "Get independent field-level evidence before another intervention.", "The same diagnostic already left the same criteria unresolved."
        trace = None
        if policy or session or choice == "stop" or (previous and previous["choice"] == "transfer"):
            from transfer_trace import inspect_trace
            try:
                trace = inspect_trace(root, json.loads(policy[0]) if policy else None, session, chat, artifact)
            except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
                trace = dict(status="unverified", reason="Incomplete or unreadable transfer evidence (" + type(exc).__name__ + ").", session=session, calls=[])
            if trace["status"] != "pass":
                choice = "verify"
                gaps = [*gaps, "Independent tool use"]
                test = "Repeat in a fresh chat using only the registered original evidence."
                reason = trace["reason"]
            elif not gaps:
                choice, test, reason = "stop", "Review the paired outputs and the recorded source reads.", trace["reason"]
        result = dict(case=case, criteria=criteria, chat=scores[0], artifact=scores[1], proofs=refs,
                      request_sha256=artifact["request_sha256"], choice=choice, gaps=gaps,
                      next_test=test, why=reason, recorded=datetime.now(UTC).isoformat())
        if trace is not None:
            result["trace"] = trace
        db.execute("INSERT INTO observations(run,case_key,details) VALUES(?,?,?)",(run,case,json.dumps(result)))
    return result
