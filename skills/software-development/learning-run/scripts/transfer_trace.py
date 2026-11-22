"""Verify a transfer using the original Desktop turn and its completed tool calls."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from hermes_cli.artifact_inputs import open_artifact


from hermes_cli.theseus_investigation import (EVIDENCE_ONLY, INVESTIGATION_ONLY, TRIAL_CONTEXT,
                                            description, description_digest, legacy_description_digest)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def contents(path):
    with open_artifact(path) as stream:
        data = stream.read(100001)
    if len(data) > 100000:
        raise ValueError("transfer evidence exceeds 100 KB")
    return data


def state(root):
    return sqlite3.connect((root.parent / "state.db").as_uri() + "?mode=ro", uri=True)


def plan(root, db, run, case, sources, investigation=None, claim=None, max_reads=None, archive_receipts=()):
    if not 1 <= len(sources)+len(archive_receipts) <= 8:
        raise ValueError("select one to eight original evidence files or archived receipts")
    if investigation is not None and (len(investigation) != 2 or not all(re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", k) for k in investigation)):
        raise ValueError("choose a project and investigation by stable identifier")
    if claim is not None:
        if not investigation:
            raise ValueError("a claim check requires a named investigation")
        from hermes_cli.theseus_investigation import receipts
        receipts.claim_support([],claim)
    if max_reads is not None and (type(max_reads) is not int or not 1<=max_reads<=16):
        raise ValueError("select a read budget from 1 to 16")
    allowed = {str(p.resolve(strict=True)): digest(contents(p.resolve())) for p in sources}
    from hermes_cli.theseus_investigation import freeze_receipts
    frozen_receipts = freeze_receipts(root.parent,investigation,archive_receipts)
    with db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM events WHERE run=?", (run,)).fetchone():
            raise ValueError("unknown run")
        row = db.execute("SELECT policy FROM transfer_plans WHERE run=? AND case_key=?", (run, case)).fetchone()
        if row:
            policy = json.loads(row[0])
            if allowed != policy["sources"] or policy.get("investigation") != investigation or policy.get("claim") != claim or policy.get("max_reads") != max_reads or policy.get("archived_receipts",{})!=frozen_receipts:
                raise ValueError("source plan already frozen")
            return policy
        with closing(state(root)) as log:
            cursor = log.execute("SELECT coalesce(max(id),0) FROM messages").fetchone()[0]
        policy = dict(sources=allowed, after_message=cursor, version=6,
                      lookup_schema_sha256=description_digest(description()))
        if frozen_receipts:
            policy["archived_receipts"] = frozen_receipts
        if claim is not None:
            policy["claim"] = claim
        if max_reads is not None:
            policy["max_reads"] = max_reads
        if investigation:
            policy["investigation"] = investigation
            policy["native_receipt_versions"] = {"sources":2,"claim":5}
        db.execute("INSERT INTO transfer_plans VALUES(?,?,?)", (run, case, json.dumps(policy)))
    return policy


def inspect_trace(root, policy, sid, chat, artifact, *, detailed=False):
    calls = []
    def verdict(status, reason):
        return dict(status=status, reason=reason, session=sid, calls=calls)

    if not policy or not sid:
        return verdict("unverified", "Register original evidence before the chat and identify its Desktop session.")
    sources = policy["sources"]
    if any(str(Path(p).resolve()) in set(sources)|set(policy.get("archived_receipts",{}).values()) for p in (chat["artifact"], artifact["artifact"])):
        return verdict("fail", "A reviewed output was registered as original evidence.")
    with closing(state(root)) as log:
        log.row_factory = sqlite3.Row
        log.execute("BEGIN")
        session = log.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not session or session["created_source"] != "desktop" or session["parent_session_id"] or session["rewind_count"]:
            return verdict("unverified", "The supplied session is not an original, fresh Desktop chat.")
        rows = log.execute("SELECT id,role,content,tool_calls,tool_call_id,tool_name,active,compacted FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    users = [r for r in rows if r["role"] == "user"]
    if len(users) != 1 or rows[0]["id"] <= policy["after_message"] or any(r["compacted"] or not r["active"] for r in rows):
        return verdict("unverified", "A single complete fresh turn after source registration is required.")
    message = users[0]["content"]
    task = message.removeprefix(EVIDENCE_ONLY)
    candidates = [message, task, message.removeprefix(INVESTIGATION_ONLY)]
    # The approved record locator is experiment metadata, never a deliverable.
    # Strip only the exact, registered second line of a bounded Desktop prompt.
    lines = task.split("\n")
    if message.startswith(EVIDENCE_ONLY) and len(lines) >= 2:
        if lines[1] in {"Record (read_file): " + path for path in sources}:
            candidates.append("\n".join(lines[:1] + lines[2:]))
    if all(digest(text.encode()) != chat["request_sha256"] for text in candidates):
        return verdict("unverified", "The original user message differs from the reviewed request.")
    final = rows[-1]
    if final["role"] != "assistant" or final["tool_calls"] or digest((final["content"] or "").encode()) != chat["artifact_sha256"]:
        return verdict("unverified", "The reviewed answer differs from the final persisted assistant message.")

    pending = {}
    seen = set()
    evidence_hash = hashlib.sha256()
    for trace_row in rows:
        evidence_hash.update(json.dumps(list(trace_row), ensure_ascii=False).encode() + b"\n")
    trace_hash = evidence_hash.hexdigest()
    outside_attempt = False
    outside_read = None
    reads = 0
    required_claim_seen = False
    uncertain = None
    for row in rows:
        if row["tool_calls"]:
            for call in json.loads(row["tool_calls"]):
                key = call["id"]
                fn = call["function"]
                name = fn["name"]
                args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
                if key in seen or row["role"] != "assistant":
                    return verdict("unverified", "Ambiguous tool-call identity.")
                seen.add(key)
                if name == "tool_call" and set(args) == {"calls"} and isinstance(args["calls"], list) and len(args["calls"]) == 1:
                    wrapped = args["calls"][0]
                    if isinstance(wrapped, dict) and set(wrapped) == {"name","arguments"} and wrapped["name"] == "investigation_lookup":
                        name, args = wrapped["name"], wrapped["arguments"]
                path = args.get("path") if name == "read_file" else None
                item = dict(tool=name, call_id=key)
                calls.append(item)
                if path and Path(path).is_absolute():
                    item["file"] = Path(path).name
                    if path not in sources:
                        outside_attempt = True
                elif name not in ("read_file", "investigation_lookup", "tool_describe"):
                    uncertain = "A tool outside the verifiable evidence reader was invoked: " + name
                pending[key] = (name, path, args, item)
        if row["role"] == "tool":
            key = row["tool_call_id"]
            if key not in pending:
                return verdict("unverified", "Tool result has no matching call.")
            name, path, args, item = pending.pop(key)
            if row["tool_name"] != name:
                return verdict("unverified", "Tool result name disagrees with its call.")
            if name == "tool_describe":
                from hermes_cli.theseus_investigation import TOOL
                expected = policy.get("lookup_schema_sha256") or legacy_description_digest()
                if args != {"names": [TOOL]} or description_digest(json.loads(row["content"])) != expected:
                    uncertain = "Unverified discovery response."
            if name == "investigation_lookup":
                from hermes_cli.theseus_investigation import OutsidePlan, verify
                scope = policy.get("investigation")
                if scope and [args.get("project"),args.get("concept")] != scope:
                    return verdict("fail", "Investigation lookup outside the registered project and concept.") | {"trace_sha256": trace_hash}
                try:
                    returned = json.loads(row["content"])
                    versions = policy.get("native_receipt_versions")
                    if versions and returned.get("receipt_version") != versions["claim" if "claim" in args else "sources"]:
                        raise ValueError("the registered investigation receipt format changed")
                    item["citations"] = verify(root.parent,args,returned,sources,policy.get("archived_receipts"))
                    if detailed or policy.get("version",1)>=3:
                        item["request"] = args
                        if "claim_check" in returned:
                            item["claim_status"] = returned["claim_check"]["status"]
                    if policy.get("claim") is not None and "claim" in args:
                        asked, actual = policy["claim"],args["claim"]
                        if policy.get("version",1)>=4 and returned.get("receipt_version",1)>=5:
                            from hermes_cli.theseus_investigation import receipts
                            asked,actual = receipts.canonical_claim(asked),receipts.canonical_claim(actual)
                        required_claim_seen |= asked==actual
                except OutsidePlan:
                    return verdict("fail", "Investigation lookup returned a source outside the registered evidence.") | {"trace_sha256": trace_hash}
                except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                    uncertain = str(exc)
                else:
                    reads += 1
            if name == "read_file":
                if path not in sources:
                    outside_attempt = True
                    returned = json.loads(row["content"])
                    if path and returned.get("content") and not returned.get("error"):
                        outside_read = Path(path).name
                    continue
                data = contents(Path(path))
                if digest(data) != sources[path]:
                    return verdict("unverified", "The registered evidence has changed.")
                lines = data.decode().splitlines()
                result = json.loads(row["content"])
                output = result.get("content", "").splitlines()
                for line in output:
                    match = re.fullmatch(r"(\d+)\|(.*)", line)
                    if not match or not 1 <= int(match[1]) <= len(lines) or lines[int(match[1])-1] != match[2]:
                        return verdict("unverified", "Returned evidence differs from the registered text.")
                if output and not result.get("error"):
                    reads += 1
                else:
                    uncertain = "An evidence read produced no verifiable content."
    if outside_read:
        return verdict("fail", "The recorded tool result returned an unregistered file: " + outside_read) | {"trace_sha256": trace_hash}
    if outside_attempt:
        return verdict("unverified", "An unregistered read was attempted without a verified result.")
    if message.startswith(INVESTIGATION_ONLY) and any(c["tool"] not in ("investigation_lookup","tool_describe") for c in calls):
        uncertain = "The native-lookup-only protocol was not followed."
    if message.startswith(EVIDENCE_ONLY) and [c["tool"] for c in calls] != ["read_file"]:
        uncertain = "The single-read protocol was not followed."
    if message.startswith(INVESTIGATION_ONLY) or (policy.get("version",1)>=2 and message.startswith(EVIDENCE_ONLY)):
        from hermes_cli.theseus_investigation import SCOPED_TRIAL_CONTEXT, isolated_context
        context = SCOPED_TRIAL_CONTEXT if policy.get("version",1)>=6 else TRIAL_CONTEXT
        isolation = isolated_context(root.parent,sid,message,context)
        if isolation != "verified":
            uncertain = "Missing isolation receipt or ordinary project decisions were supplied: " + isolation
    elif policy.get("version",1)>=2:
        # A clean explicit tool list cannot certify inputs supplied by project memory.
        try:
            db_path = root.parent / "project-recall/records.sqlite"
            with closing(sqlite3.connect(db_path.as_uri()+"?mode=ro",uri=True)) as project_db:
                if project_db.execute("SELECT 1 FROM memory_turn_reads WHERE session=? LIMIT 1",(sid,)).fetchone():
                    uncertain = "Ordinary project decisions were supplied; independence from them is unverified."
        except sqlite3.Error:
            uncertain = "Project-context provenance is unavailable."
    if policy.get("claim") is not None and not required_claim_seen:
        uncertain = "The registered field and scope were not inspected in a verified native lookup."
    if policy.get("max_reads") is not None and reads > policy["max_reads"]:
        return verdict("fail","The completed evidence reads exceeded the registered budget.") | {"trace_sha256":trace_hash}
    if pending or uncertain or not reads:
        return verdict("unverified", uncertain or "Missing completed evidence read.")
    result = verdict("pass", ("Investigation receipts replayed against the registered original evidence."
                              if any(c["tool"] == "investigation_lookup" for c in calls) else
                              "Every recorded call returned text from a registered evidence file."))
    result.update(model=session["model"], trace_sha256=trace_hash)
    return result
