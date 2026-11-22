import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "plugins/theseus-project-memory/__init__.py"
spec = importlib.util.spec_from_file_location("ordinary_hooks", FILE)
hooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hooks)


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / "skills").symlink_to(ROOT / "skills")
    monkeypatch.setattr(hooks, "get_hermes_home", lambda: tmp_path)
    return tmp_path


def change(home, project, session, key, value, edition=1, other=None):
    cls = hooks.runtime(home).Memory
    m = cls(home, project)
    with m.db:
        old = m.resolve("interpretation", edition)
        changes = [
            dict(
                op="decide",
                name="interpretation",
                previous=old["id"] if old else None,
                value=value,
                edition=edition,
            )
        ]
        if other:
            changes.append(other)
        m.revision(key, value, f"chat:{session}:message:1", changes)
    m.db.close()


def start(session, turn, text):
    return hooks.on_turn(session_id=session, turn_id=turn, user_message=text)["context"]


def payload(context):
    return json.loads(context.splitlines()[-1])


def save(session, turn, request, tool="write_file"):
    return hooks.before_tool(
        tool_name=tool, session_id=session, turn_id=turn, api_request_id=request
    )


def test_ordinary_resumed_and_fresh_chat_receive_only_the_current_interpretation(home):
    start("author", "draft", "For project FERRO draft the bench note.")
    change(home, "ferro", "author", "draft", "Old tentative explanation")
    old = start("author", "pause", "Polish the heading.")
    assert "Old tentative explanation" in old
    start("editor", "correct", "Correction for FERRO")
    change(
        home,
        "ferro",
        "editor",
        "changed",
        "New best explanation",
        other=dict(
            op="thesis",
            name="old",
            value="Old tentative explanation",
            status="rejected",
        ),
    )
    for context in [
        start("author", "resume", "Finish the file."),
        start("fresh", "query", "What explains the FERRO observation?"),
    ]:
        data = payload(context)
        assert data["project"] == "ferro"
        assert data["latest_general_decisions"][0]["value"] == "New best explanation"
        assert "Old tentative explanation" not in context
    assert save("author", "resume", "new") is None


def test_stale_native_action_and_parallel_batch_cannot_slip_through(home):
    change(home, "book", "a", "first", "First")
    start("a", "generating", "Book preflight")
    change(home, "book", "b", "correct", "Second")
    for tool in ["patch", "terminal", "execute_code", "write_file"]:
        result = save("a", "generating", "stale-batch", tool)
        assert result["action"] == "block" and "Second" in result["message"]
    assert save("a", "generating", "replanned", "patch") is None


def test_other_project_and_multiple_matches(home):
    change(home, "alpha", "a", "start", "Alpha decision")
    change(home, "beta", "b", "start", "Beta decision")
    assert payload(start("a", "one", "alpha artwork"))["project"] == "alpha"
    assert "Alpha decision" not in start("a", "two", "Compare alpha and beta")
    assert "Alpha decision" not in start("a", "three", "Continue.")
    assert "Beta decision" not in start("new", "four", "An unrelated note")
    assert (
        payload(start("a", "five", "Switch to project FERN from alpha"))["project"]
        == "fern"
    )
    assert save("a", "five", "normal") is None


def test_numeric_scope_and_no_silent_clipping(home):
    change(home, "proof", "a", "base", "Screen")
    change(home, "proof", "a", "seven", "Hard proof", edition=7)
    data = payload(start("s", "edit", "proof"))
    assert data["latest_general_decisions"][0]["from_edition"] == 7
    change(home, "large", "a", "long", "x" * 8000)
    data = payload(start("s", "new", "large"))
    assert data["truncated"] and not data["latest_general_decisions"]


def test_fresh_investigation_lookup_and_collision(home):
    question = dict(
        op="investigate",
        name="interpretation",
        question="What explains the earliest bench tests?",
        scope="Initial run",
        discriminator="Find the raw observations.",
    )
    change(home, "first", "source", "initial", "Revised reading", other=question)
    assert (
        payload(start("fresh", "1", "Explain our earliest bench tests."))["project"]
        == "first"
    )
    change(home, "second", "source2", "initial", "Different reading", other=question)
    ask = hooks.on_turn(
        session_id="another",
        turn_id="1",
        user_message="Explain our earliest bench tests.",
    )
    assert ask == {
        "action": "block",
        "message": "Which project do you mean — first or second?",
    }
    context = start("another", "2", "second")
    assert hooks.CAUSAL_CHECK in context
    resolved = payload(context)
    assert resolved["project"] == "second"
    assert resolved["investigation_briefs"][0]["current"] == "Different reading"


def test_fresh_unregistered_case_gets_causal_check_without_project_data(home):
    context = start("fresh", "1", "Why are there streaks in the scans?")
    assert hooks.CAUSAL_CHECK in context
    assert "Current project state (data)" not in context


def test_older_source_preserves_recent_window_and_respects_lookup_bounds(monkeypatch):
    monkeypatch.setattr(hooks, "semantic_scores", lambda *_: None)

    def exhibit(i, excerpt):
        return dict(kind="specimen", excerpt=excerpt, limitation="One sample",
                    citation=f"record:{i}:snapshot:1")

    evidence = [exhibit(i, "Packing inspection") for i in range(40)]
    evidence[3]["excerpt"] = "Abrasion test outside search window"
    evidence[4]["excerpt"] = "Abrasion test within window"
    evidence[5]["excerpt"] = "Abrasion test too large"
    evidence[5]["limitation"] = "λ" * 700
    evidence[-1]["excerpt"] = "Abrasion check pending"
    assert hooks.older_exhibit(evidence, "abrasion", "packing inspection") == evidence[4]
    assert hooks.older_exhibit(evidence, "packing inspection", "packing inspection") is None
    assert hooks.older_exhibit(evidence, "unrelated", "packing inspection") is None
    assert hooks.older_exhibit(evidence[-4:], "abrasion", "packing inspection") is None


def test_semantic_supplement_requires_separation_and_preserves_source(monkeypatch):
    source = dict(excerpt="Flow fell by twelve litres per minute.", limitation="Single meter")
    other = dict(excerpt="Cover had a glossy finish.", limitation="Visual only")
    evidence = [source, other] + [dict(excerpt="Recent update")] * 4
    def select(scores):
        def score(query, excerpts):
            assert query == "water passing through slowed down"
            assert excerpts == [other["excerpt"], source["excerpt"]]
            return scores
        monkeypatch.setattr(hooks, "semantic_scores", score)
        return hooks.older_exhibit(evidence, "Water passing through slowed down", "Meter history?")
    assert select([0.10, 0.51]) is source
    assert select([0.49, 0.51]) is None
    assert select([0.01, 0.16]) is None
    assert select(None) is None


def test_condition_screen_separates_observation_from_exclusions():
    source = {"excerpt": "At 4.5 °C the instrument recorded 8 counts.",
              "limitation": "No trial at −4.5 °C. One run without the cover."}
    for condition, expected in [("at 4.5 °C", True), ("at −4.5 °C", False),
                                ("with the cover", False), ("without the cover", True)]:
        assert hooks.condition_supported(source, condition) is expected
    assert hooks.requested_conditions(
        "Under reduced pressure, what was recorded? What can we authorize?"
    ) == ["Under reduced pressure"]


def test_matching_mode_cannot_override_excluded_subject():
    report = {"excerpt": "The tabletop counter registered paired arrivals.",
              "limitation": "No installed equipment was tested."}
    assert hooks.explicitly_excluded(report, "What happened on the installed counter?")
    assert not hooks.explicitly_excluded(report, "What happened on the tabletop counter?")


def test_one_batch_receipt_scopes_quotes_and_projects():
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE docs(id INTEGER, name TEXT, path TEXT, digest TEXT, data BLOB);
        CREATE VIEW source_status AS SELECT id AS doc, 'current' AS status FROM docs;
        CREATE TABLE spans(doc INTEGER, body TEXT, first_line INTEGER, last_line INTEGER);
        CREATE TABLE names(name TEXT, anchor TEXT);
        INSERT INTO names VALUES ('a','a'),('b','b');
    """)
    import hashlib
    raw = b"Measured 12. Other condition: 7."
    digest = hashlib.sha256(raw).hexdigest()
    db.execute("INSERT INTO docs VALUES (1,'a','/trial/log.txt',?,?)", (digest,raw))
    db.execute("INSERT INTO spans VALUES (1,'Measured 12.','1','1')")
    citation = f"record:1:snapshot:1:sha256:{digest}"
    def item(quote):
        return {"citation": citation, "excerpt": quote, "limitation": "One shift", "kind": "specimen"}
    valid, absent = item("Measured 12."), item("Measured 20.")
    inputs = [({}, [valid, absent])]
    traced = []
    db.set_trace_callback(traced.append)
    found = hooks.lookup_exhibits(db, "a", inputs)
    assert len(traced) == 1
    assert (citation, absent["excerpt"]) not in found
    assert found[citation, valid["excerpt"]]["coverage"] == "whole section"
    assert not hooks.lookup_exhibits(db, "b", inputs)
    db.execute("UPDATE spans SET body='Measured 12.' || char(10)")
    assert hooks.lookup_exhibits(db, "a", inputs)[citation, valid["excerpt"]]["coverage"] == "whole section"
    db.execute("UPDATE spans SET body='Measured 12. Other condition: 7.'")
    assert "Other condition" in hooks.lookup_exhibits(db, "a", inputs)[citation, valid["excerpt"]]["section"]
    db.execute("UPDATE spans SET body='Measured 20.'")
    assert not hooks.lookup_exhibits(db, "a", inputs)  # Resolves in index, absent in original.
    db.execute("UPDATE docs SET data=?", (b"Measured 20.",))
    assert not hooks.lookup_exhibits(db, "a", inputs)  # Changed bytes under the old hash.



def test_live_comparison_uses_bounded_distinct_checked_excerpts():
    def item(n, text):
        return dict(citation=str(n), excerpt=text, limitation="One session", kind="specimen")

    visible = item(1, "Receiver counted 17 signals")
    second = item(2, "Receiver counted 12 signals")
    third = item(3, "Receiver counted 3 signals")
    over = item(4, "Receiver counted signals " * 100)
    candidates = [visible, second, second, third, over, item(5, "Packaging arrived")]
    assert hooks.comparison_sources("How many signals did the receiver count?", candidates, [visible]) == []
    assert hooks.comparison_sources("Compare receiver signals", candidates, [visible]) == [third, second]


def test_display_citations_preserves_unverified_status_and_uses_verified_id():
    citation = "record:1:snapshot:2:sha256:" + "a" * 64
    older = {"citation": citation}
    unverified = {"citation": citation, "verification": "unverified"}
    brief = {"record_lookup": [{"id": "record:1:snapshot:2"}], "evidence": [unverified],
             "older_source": older, "comparison_sources": [{"citation": citation}]}
    hooks.display_citations(brief)
    assert older["citation"] == brief["comparison_sources"][0]["citation"] == "record:1:snapshot:2"
    assert unverified["citation"] == citation


def test_explicit_single_investigation_and_ambiguous_project(home):
    import json
    with hooks.connect(home) as db:
        def add(project,concept):
            db.execute("INSERT INTO memory_events(project,request,payload,evidence,citation) VALUES(?,?,?,?,?)",
                       (project,concept,json.dumps([{"op":"investigate","name":concept}]),"test","fixture"))
        add("copper-gallery","air-test")
        assert hooks.project_investigations(db,"copper-gallery","Update the Copper Gallery handoff.",[])==["air-test"]
        assert hooks.project_investigations(db,"copper-gallery","Unrelated question",[])==[]
        add("copper-gallery","mount-test")
        assert hooks.project_investigations(db,"copper-gallery","Update the Copper Gallery handoff.",[])==[]
        assert hooks.project_investigations(db,"copper-gallery","Update the mount test handoff.",[])==["mount-test"]


def research_catalog(home, names):
    import sqlite3
    from contextlib import closing
    (home / "research").mkdir()
    with closing(sqlite3.connect(home / "research/library.sqlite")) as db, db:
        db.execute("CREATE TABLE sources(project TEXT, body TEXT)")
        db.executemany("INSERT INTO sources VALUES(?,?)", [(n, "sensitive exhibit") for n in names])


def test_ambiguous_research_name_stops_discovery_and_resumes_in_order(home):
    research_catalog(home, ["North Pier", "North-Pier", "Pier"])
    q = "What deposit is recorded for the north_pier project?"
    ctx = start("chat", "question", q)
    assert '["North Pier", "North-Pier"]' in ctx and "sensitive exhibit" not in ctx
    for tool in ("research_library", "terminal", "read_file", "clarify"):
        assert save("chat", "question", "", tool)["action"] == "block"
        assert save("elsewhere", "question", "", tool) is None
    assert "numbered choices" in start("chat", "retry", "option 8")
    ctx = start("chat", "selection", "the second one")
    assert '"North-Pier"' in ctx and "preceding unanswered request" in ctx
    assert save("chat", "selection", "", "research_library") is None


def test_literal_name_unique_alias_topic_switch_and_project_memory(home):
    research_catalog(home, ["North Pier", "North-Pier", "Silver Orchard"])
    change(home, "silver-orchard", "old", "rule", "Use revised hours")
    assert "numbered choices" in start("a", "1", "Use project north_pier please.")
    assert "numbered choices" not in start("a", "2", "Help me plan a grocery list.")
    assert save("a", "2", "", "research_library") is None
    ctx = start("a", "3", 'Use project "North-Pier" for the desk deposit.')
    assert '"North-Pier"' in ctx and "numbered choices" not in ctx
    ctx = start("a", "4", "What hours are recorded in the silver_orchard project?")
    assert '"Silver Orchard"' in ctx and "Use revised hours" in ctx
    assert "Use revised hours" not in start("a", "5", "Use project North-Pier for this.")
    assert "Use revised hours" not in start("a", "6", "Continue.")


def test_project_reference_boundaries():
    names = ["Bay", "Bay Port", "Bay-Port", "Old Bay", "New Bay"]
    match = hooks.research_routing.named_projects
    assert match("For the bay_port project, check my notes.", names) == names[1:3]
    assert match("Compare project Old Bay with project New Bay.", names) == []
    for question in ("Open /tmp/bay_port project notes", "Project bay_port.json", "Translate the word project."):
        assert match(question, names) == []


def test_unrecognized_qualifier_never_reads_the_neighbor(home):
    research_catalog(home, ["Harbor Review", "Harbor-Review", "Orchard Sessions"])
    for i,q in enumerate(("What deposit did the East Harbor Review project record?",
                          "What deposit did the east harbor review project record?",
                          'What fee is in project "Upper Harbor Review Annex"?',
                          "What did project Harbor Review Annex record?")):
        session = f"near-{i}"
        ctx = start(session, "ask", q)
        assert "did not match" in ctx and '"Harbor Review"' in ctx
        assert save(session,"ask","","research_library")["action"] == "block"
        assert "declined" in start(session,"cancel","Neither.")
        assert save(session,"cancel","","research_library") is None
    ctx = start("valid","1","What has project Orchard Sessions approved this week?")
    assert '"Orchard Sessions"' in ctx and "did not match" not in ctx
    assert "did not match" not in start("new","1","Create a new project East Harbor Review.")


def test_explicit_comparison_and_both_after_clarification(home):
    research_catalog(home, ["Salt Pier", "Salt-Pier"])
    for q in ("Compare the Salt Pier and Salt-Pier projects.", "Compare project Salt Pier with project Salt-Pier."):
        ctx = start("comparison", "direct", q)
        assert '["Salt Pier", "Salt-Pier"]' in ctx and "separately" in ctx
        assert save("comparison","direct","","research_library") is None
    assert "numbered choices" in start("chat","ask","Use project salt_pier for the comparison.")
    assert "separately" in start("chat","selected","Compare both.")
    assert save("chat","selected","","research_library") is None
    assert hooks.research_routing.named_projects("How does this project compare to the prices at Salt Pier?",["Salt Pier"])==[]


def test_cross_project_source_comparison_keeps_corrections_attributed(home):
    research_catalog(home, ["Orchard Sessions", "Music Class", "Run Deck", "Run-Deck"])
    change(home,"orchard-sessions","setter","first","Meet at 15:05")
    change(home,"run-deck","setter","second","Ambiguous ledger identity")
    ctx = start("comparison","1","Compare the Orchard Sessions and Music Class projects.")
    decisions = json.loads(ctx.split("Current project decisions (data): ")[1])
    assert set(decisions) == {"Orchard Sessions"}
    assert decisions["Orchard Sessions"]["latest_general_decisions"][0]["value"] == "Meet at 15:05"
    ctx = start("collision","1","Compare the Run Deck and Run-Deck projects.")
    assert "Ambiguous ledger identity" not in ctx
