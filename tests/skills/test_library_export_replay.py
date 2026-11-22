"""A saved artifact is a retrieval yardstick only when its original tool receipts agree."""

import json
from pathlib import Path

import pytest

from hermes_constants import get_hermes_home
from hermes_state import SessionDB
from hermes_cli.theseus_research import library, plugin


@pytest.fixture
def job():
    project = "Meter calibration"

    def op(action, **args):
        return library.execute(dict(project=project, action=action, **args))

    body = (
        "Archive index and conditions. " * 100
        + "\nPressure recovery was 32 seconds. This was an ambient run."
    )
    source = library.execute(
        dict(project="Records", action="capture", title="Recovery series", text=body)
    )
    op(
        "discovery_pin",
        origin_project="Records",
        source=source["source"],
        edition=source["edition"],
    )
    source_fields = dict(source=source["source"], edition=source["edition"])
    query = dict(query="pressure recovery ambient")
    search = op("search", **query)
    read = op("read", **source_fields)
    cells = {
        "Time": dict(
            value="32 seconds",
            edition=source["edition"],
            quote="Pressure recovery was 32 seconds.",
        ),
        "Condition": dict(
            value="ambient", edition=source["edition"], quote="This was an ambient run."
        ),
    }
    brief_args = dict(
        title="Recovery decision",
        question="What was measured?",
        columns=list(cells),
        rows=[dict(name="Sensor", cells=cells)],
    )
    brief = op("brief_create", **brief_args)
    export = op("brief_export", brief=brief["brief"])
    store = SessionDB(Path(get_hermes_home()) / "state.db")

    def turn(
        sid="trial",
        *,
        do_search=True,
        direct_read=True,
        later=False,
        corrupt=None,
        author=True,
        fake_digest=False,
    ):
        store.create_session(sid, "desktop")
        store.append_message(sid, "user", "Review the sensor.")

        def tool(id, name, args, reply):
            call = dict(
                id=id,
                type="function",
                function=dict(
                    name="tool_call",
                    arguments=json.dumps(
                        dict(
                            calls=[
                                dict(name=name, arguments=dict(project=project, **args))
                            ]
                        )
                    ),
                ),
            )
            store.append_message(sid, "assistant", tool_calls=[call])
            store.append_message(
                sid,
                "tool",
                content=json.dumps(reply),
                tool_name=name,
                tool_call_id=("mismatched" if corrupt == id else id),
            )

        if do_search:
            observed = search
            if fake_digest:
                observed = {
                    **search,
                    "sources": [
                        {**r, "digest": "untrusted", "excerpt": body}
                        for r in search["sources"]
                    ],
                }
            tool("q", "research_library", dict(action="search", **query), observed)
        if direct_read:
            tool("r", "research_library", dict(action="read", **source_fields), read)
        if author:
            tool(
                "a", "research_brief", dict(action="brief_create", **brief_args), brief
            )
        if later:
            tool("q2", "research_library", dict(action="search", **query), search)
        tool(
            "x",
            "research_brief",
            dict(action="brief_export", brief=brief["brief"]),
            export,
        )

    def replay(project_name=project):
        return plugin.desktop_request(
            dict(
                project=project_name,
                action="search_trial_export_check",
                brief=brief["brief"],
                revision=1,
            )
        )

    yield turn, replay, Path(export["path"]), source
    store.close()


def test_artifact_replay_counts_each_distinct_cited_cell_and_persists(job):
    turn, replay, _, _ = job
    turn(later=True)
    check = replay()
    r = check["result"]
    assert r["queries"] == ["pressure recovery ambient"] and r["later_searches"] == 1
    assert r["editions"] == {"original": 1, "current": 1, "focused": 1}
    assert len(r["cells"]) == 2
    for c in r["cells"]:
        assert c["search"] == {"original": False, "current": False, "focused": True}
        assert c["original_read"] and "quote" not in c and c["quote_digest"]
    assert replay()["id"] == check["id"]
    listed = plugin.desktop_request(
        dict(project="Meter calibration", action="search_trial_list")
    )
    assert [s["id"] for s in listed["checks"]] == [check["id"]]
    isolated = plugin.desktop_request(
        dict(project="Elsewhere", action="search_trial_list")
    )
    assert isolated["checks"] == []


def test_no_credit_for_wrong_source_digest(job):
    turn, replay, _, _ = job
    turn(fake_digest=True, direct_read=False)
    assert all(
        not c["search"]["original"] and not c["original_read"]
        for c in replay()["result"]["cells"]
    )


@pytest.mark.parametrize(
    "kwargs,message",
    [
        (dict(author=False), "No unique saved turn"),
        (dict(corrupt="q"), "linked to its request"),
        (dict(do_search=False, later=True), "completed project searches"),
    ],
)
def test_rejects_posthoc_missing_or_mismatched_proof(job, kwargs, message):
    turn, replay, _, _ = job
    turn(**kwargs)
    with pytest.raises(ValueError, match=message):
        replay()


def test_unrelated_export_and_project_isolation(job):
    turn, replay, _, _ = job
    turn("author")
    turn("re-export", author=False)
    assert replay()["result"]["title"] == "Recovery decision"
    with pytest.raises(ValueError, match="project"):
        replay("Elsewhere")


def test_ambiguous_origin_and_physical_csv_edit_are_refused(job):
    turn, replay, path, _ = job
    turn("first")
    turn("second")
    with pytest.raises(ValueError, match="No unique saved turn"):
        replay()
    path.write_text("A different saved result\n")
    with pytest.raises(ValueError, match="File differs"):
        replay()


def test_revision_outside_current_collection_is_identified(job):
    turn, replay, _, source = job
    turn()
    newer = library.execute(
        dict(
            project="Records",
            action="capture",
            title="Recovery series",
            text="Replacement report.",
            origin=library.execute(
                dict(project="Records", action="read", source=source["source"])
            )["origin"],
        )
    )
    with library.connection() as db:
        db.execute(
            "INSERT INTO research_pin_changes(project,source,previous,chosen) VALUES(?,?,?,?)",
            (
                "Meter calibration",
                source["source"],
                source["edition"],
                newer["edition"],
            ),
        )
        db.execute(
            "UPDATE research_pins SET edition=? WHERE project=? AND source=?",
            (newer["edition"], "Meter calibration", source["source"]),
        )
    r = replay()["result"]
    assert all(
        not c["current"] and not c["search"]["focused"] and c["original_read"]
        for c in r["cells"]
    )


def test_batch_finds_gain_and_leaves_other_work_untouched(job):
    turn, replay, path, _ = job
    turn()
    original = path.read_bytes()
    scan = plugin.desktop_request(
        dict(action="search_trial_scan_exports", project="Meter calibration")
    )
    assert path.read_bytes() == original and scan["before"] is None
    checked = replay()
    assert (
        scan["recommended"] == checked["id"]
        and scan["entries"][0]["finding"]["kind"] == "gain"
    )
    assert (
        plugin.desktop_request(
            dict(action="search_trial_scan_exports", project="Empty")
        )["entries"]
        == []
    )


def test_batch_pages_are_bounded_even_when_csvs_are_missing(job):
    turn, _, _, _ = job
    turn()
    for i in range(4):
        library.execute(
            dict(
                action="brief_create",
                project="Meter calibration",
                title="Unexported " + str(i),
                question="How?",
            )
        )
    ask = lambda **extra: plugin.desktop_request(
        dict(action="search_trial_scan_exports", project="Meter calibration", **extra)
    )
    first = ask()
    assert (
        len(first["entries"]) == 3 and first["recommended"] is None and first["before"]
    )
    second = ask(before=first["before"])
    assert (
        len(second["entries"]) == 2 and second["recommended"] and not second["before"]
    )
    assert not {e["brief"] for e in first["entries"]} & {
        e["brief"] for e in second["entries"]
    }
    with pytest.raises(ValueError):
        ask(before=True)


def test_next_experiment_values_evidence_losses_above_gains():
    from hermes_cli.theseus_research.search_exports import next_check

    def cell(current, focused, seen=True, active=True):
        return dict(
            current=active,
            search=dict(original=seen, current=current, focused=focused),
            original_read=False,
            row="Result",
            column="Quantity",
            edition=1,
        )

    assert next_check([cell(False, True), cell(True, False)])["kind"] == "regression"
    assert next_check([cell(False, False), cell(False, True)])["kind"] == "gain"
    assert next_check([cell(False, False)])["kind"] == "shared gap"
    assert next_check([cell(False, False, active=False)])["kind"] == "edition changed"
    assert next_check([cell(True, False, seen=False)])["kind"] == "unverified origin"
    assert next_check([cell(True, True)])["kind"] == "tie"


def test_parallel_search_cannot_be_credited_to_the_simultaneous_save(job):
    import sqlite3

    turn, replay, _, _ = job
    turn()
    with sqlite3.connect(Path(get_hermes_home()) / "state.db") as db:
        calls = [
            (row, json.loads(value))
            for row, value in db.execute(
                "SELECT id,tool_calls FROM messages WHERE role='assistant' AND tool_calls IS NOT NULL ORDER BY id"
            )
        ]
        search_id, search = next((r, a) for r, a in calls if a[0]["id"] == "q")
        create_id, create = next((r, a) for r, a in calls if a[0]["id"] == "a")
        db.execute(
            "UPDATE messages SET tool_calls=? WHERE id=?",
            (json.dumps(search + create), search_id),
        )
        db.execute("UPDATE messages SET active=0 WHERE id=?", (create_id,))
    with pytest.raises(ValueError, match="completed project searches"):
        replay()


def test_export_does_not_require_a_later_sibling_receipt(job):
    import sqlite3

    turn, replay, _, _ = job
    turn()
    with sqlite3.connect(Path(get_hermes_home()) / "state.db") as db:
        row, encoded = db.execute(
            "SELECT id,tool_calls FROM messages WHERE role='assistant' AND tool_calls IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        export = json.loads(encoded)
        concurrent = dict(
            id="read-after-export",
            function=dict(
                name="research_brief",
                arguments=json.dumps(
                    dict(action="brief_read", project="Meter calibration")
                ),
            ),
        )
        db.execute(
            "UPDATE messages SET tool_calls=? WHERE id=?",
            (json.dumps(export + [concurrent]), row),
        )
    assert len(replay()["result"]["queries"]) == 1


def assist(action, project="Meter calibration", **params):
    return plugin.desktop_request(dict(action="assist_" + action, project=project, **params))


def enqueue_export(session):
    project = "Meter calibration"
    doc = library.execute(dict(action="brief_list", project=project))["briefs"][0]
    return library.execute(dict(action="brief_export", project=project, brief=doc["brief"]),
                           resolve_tool_reads=True, session_id=session)


def test_ordinary_export_schedules_one_comparison_in_its_original_turn(job):
    from hermes_cli.theseus_research import assistance
    turn, _, path, _ = job
    turn("owner")
    turn("irrelevant copy")
    original = path.read_bytes()
    enqueue_export("owner")
    enqueue_export("owner")
    assert assist("chat", session="irrelevant copy")["items"] == []
    assistance.after_turn("owner")
    result, = assist("project")["items"]
    assert (result["kind"], result["trigger"]) == ("gain", "turn completed")
    assert assist("chat", session="owner")["items"] == [result]
    proof = assist("evidence", id=result["id"])
    assert proof["file_digest"] == __import__("hashlib").sha256(original).hexdigest()
    assert not proof["choice"]["focused"]
    assert path.read_bytes() == original
    state = SessionDB(Path(get_hermes_home()) / "state.db")
    try:
        state.append_message("owner", "user", "When is lunch?")
    finally:
        state.close()
    assert not assist("chat", session="owner")["items"]
    assert assist("project")["items"] == [result]


def test_modes_are_local_and_switching_off_cancels_an_offer(job):
    turn, _, _, _ = job
    turn("guest")
    assist("set_mode", expected="automatic", mode="suggest")
    enqueue_export("guest")
    offer, = assist("chat", session="guest")["items"]
    assert offer["state"] == "offered" and offer["check"] is None
    assert assist("project", "Another project") == {"mode":"automatic","items":[]}
    with pytest.raises(ValueError, match="project"):
        assist("check", "Another project", id=offer["id"])
    assist("set_mode", expected="suggest", mode="off")
    assist("check", id=offer["id"])
    assert assist("project")["items"][0]["state"] == "cancelled"
    assert not assist("chat", session="guest")["items"]
    turn("off")
    enqueue_export("off")
    assert len(assist("project")["items"]) == 1


def test_explicit_check_requires_user_choice_to_change_policy(job):
    turn, _, _, _ = job
    turn("offered")
    assist("set_mode", expected="automatic", mode="suggest")
    enqueue_export("offered")
    offer, = assist("chat", session="offered")["items"]
    assist("check", id=offer["id"])
    checked, = assist("project")["items"]
    assert checked["trigger"] == "user requested"
    proof = assist("evidence", id=checked["id"])
    assert not proof["choice"]["focused"]
    with pytest.raises(ValueError):
        assist("apply", id=checked["id"], expected=-1)
    assert assist("apply", id=checked["id"], expected=proof["choice"]["id"])["choice"]["focused"]
    assert not assist("chat", session="offered")["items"]


def test_dismissal_suppresses_same_evidence_in_another_job(job):
    turn, _, _, _ = job
    turn("first")
    enqueue_export("first")
    first, = assist("chat", session="first")["items"]
    assist("dismiss", id=first["id"])
    turn("second")
    enqueue_export("second")
    assert not assist("chat", session="second")["items"]
    assert all(i["dismissed"] for i in assist("project")["items"])
