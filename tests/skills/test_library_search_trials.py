"""Counterfactual evidence comparison, scoped to an immutable library and one session."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_cli.theseus_research import library, plugin, search_focus, search_trials


def call(action, project="Survey", **args):
    return plugin.desktop_request(
        dict(action="search_trial_" + action, project=project, **args)
    )


def note(title, text, project="Survey", **args):
    return library.execute(
        dict(action="capture", project=project, title=title, text=text, **args)
    )


def agent(query, session=None, project="Survey"):
    return library.execute(
        dict(action="search", project=project, query=query),
        resolve_tool_reads=True,
        session_id=session,
    )


@pytest.fixture
def registered():
    first = note(
        "Slip 42",
        "Archive cover. " * 120 + "\nVesper. Signed offset 275.",
        project="Shelf",
    )
    library.execute(
        dict(
            project="Survey",
            action="discovery_pin",
            origin_project="Shelf",
            source=first["source"],
            edition=first["edition"],
        )
    )
    other = note("Vesper month-end forecast", "The statement carried 960 on July 18.")
    noise = note("Desks", "The office closed at six.")
    q = "What is the Vesper month-end forecast?"
    roster = [
        dict(edition=s["edition"], role="required", quote=quote)
        for s, quote in ((first, "Signed offset 275."), (other, "960 on July 18."))
    ]
    case = call("freeze", title="July balance", question=q, judgments=roster)["case"]
    return case, q, roster, noise


def test_frozen_comparison_distinguishes_unknown_from_irrelevant(registered):
    case, q, roster, noise = registered
    assert (
        call("freeze", title="July balance", question=q, judgments=roster)["case"]
        == case
    )
    with pytest.raises(ValueError, match="already frozen"):
        call("freeze", title="July balance", question="Different?", judgments=roster)
    a = call("replay", case=case, query=q)["results"]
    assert a["focused"]["passages"] == 2 and a["current"]["passages"] == 1
    assert a["current"]["unjudged"] == a["focused"]["unjudged"] == 1
    assert a["current"]["unrelated"] == 0
    assert noise["edition"] in {s["edition"] for s in a["current"]["sources"]}
    assert [s["edition"] for s in a["current"]["sources"]] == [
        s["edition"] for s in a["focused"]["sources"]
    ]
    assert (
        0
        < a["focused"]["excerpt_bytes"] - a["current"]["excerpt_bytes"]
        <= search_focus.SUPPLEMENT_BYTES
    )


@pytest.mark.parametrize("terms", [[], ["the", "and", "of"], ["unrecorded"]])
def test_no_body_anchor_preserves_the_opening(terms):
    assert search_focus.excerpt_start("opening text " * 100, terms) == 0


def test_original_offsets_with_unicode_and_late_anchor():
    body = (
        ("Straße İß. " * 170)
        + "\nMünchen. X12 without pressure: 26 kPa. "
        + ("Archive. " * 100)
    )
    start = search_focus.excerpt_start(body, ["MÜNCHEN", "X12", "without", "pressure"])
    assert "München. X12 without pressure: 26 kPa." in body[start : start + 220]


@pytest.mark.parametrize(
    "bad",
    [
        {"role": [], "edition": 1},
        {"role": "required", "edition": True},
        {"role": "required", "edition": 1, "quote": "absent"},
        {"role": "required", "edition": 99, "quote": "Signed offset 275."},
    ],
)
def test_invalid_rosters_fail_without_creating_cases(registered, bad):
    with pytest.raises(ValueError):
        call("freeze", title="bad", question="Q", judgments=[bad])
    assert len(call("list")["cases"]) == 1


def test_passage_must_actually_be_returned():
    body = "Budget results. " + ("unrelated prose " * 241) + "Signed reduction sixty."
    s = note("Budget", body)
    case = call(
        "freeze",
        title="Tail",
        question="Budget",
        judgments=[
            dict(edition=s["edition"], role="required", quote="Signed reduction sixty.")
        ],
    )["case"]
    both = call("replay", case=case)["results"]
    for r in both.values():
        assert r["passages"] == 0 and r["missing"] == [s["edition"]]
        assert r["sources"][0]["scope"] == "excerpt only"
        assert r["sources"][0]["bytes"] < len(body.encode())


def test_one_session_gets_window_no_agent_gold_and_limit(registered):
    case, query, _, _ = registered
    incumbent = agent(query)
    wid = call("arm", case=case, arm="focused")["window"]
    assert call("arm", case=case, arm="focused")["window"] == wid
    assert call("stop", window=wid + 1) == {"stopped": False}
    assert agent(query, None) == incumbent
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda sid: agent(query, sid), ("alpha", "beta")))
    assert sorted(a == incumbent for a in answers) == [False, True]
    owner = ("alpha", "beta")[answers.index(next(a for a in answers if a != incumbent))]
    for _ in range(search_trials.MAX_LOOKUPS - 1):
        payload = agent(query, owner)
        assert "judgments" not in json.dumps(payload) and "required" not in payload
    assert call("list")["window"] is None and agent(query, owner) == incumbent
    assert len(call("list")["cases"][0]["pairs"]) == search_trials.MAX_LOOKUPS


def test_library_change_ends_window_and_preserves_historical_pairs(registered):
    case, q, _, _ = registered
    call("arm", case=case, arm="focused")
    agent(q, "one")
    note("One more", "This was added later")
    assert agent(q, "one") == agent(q)
    with pytest.raises(ValueError, match="still matches"):
        call("replay", case=case)
    state = call("list")
    assert not state["cases"][0]["current"] and len(state["cases"][0]["pairs"]) == 1
    assert not state["window"]


def test_expiry_other_projects_and_agent_capability(registered, monkeypatch):
    case, q, _, _ = registered
    with pytest.raises(ValueError):
        call("arm", project="Other", case=case, arm="focused")
    call("arm", case=case, arm="focused")
    with library.connection() as db:
        db.execute("UPDATE search_trial_windows SET expires=0 WHERE ended IS NULL")
    assert agent(q, "first") == agent(q)
    assert not call("list")["window"]
    assert "error" in json.loads(
        plugin.run(dict(action="search_trial_list", project="Survey"))
    )
    assert not agent(q, "first", project="Other")["sources"]


def test_pinned_revision_is_the_only_searchable_shared_body():
    from hermes_cli.theseus_research import discovery

    origin = note(
        "Contract",
        "Vesper signed reduction 275.",
        project="Supplier",
        origin="note:fixed",
    )
    with library.connection() as db:
        discovery.operate(
            db,
            "Survey",
            dict(
                action="discovery_pin",
                source=origin["source"],
                edition=origin["edition"],
                origin_project="Supplier",
            ),
        )
    note(
        "Contract",
        "Future cobalt allowance 900.",
        project="Supplier",
        origin="note:fixed",
    )
    case = call(
        "freeze",
        title="Pin",
        question="Vesper reduction amount",
        judgments=[
            dict(
                edition=origin["edition"],
                role="required",
                quote="signed reduction 275.",
            )
        ],
    )["case"]
    assert call("replay", case=case)["results"]["focused"]["passages"] == 1
    for arm in call("replay", case=case, query="future cobalt allowance")[
        "results"
    ].values():
        assert not arm["sources"]


def test_project_policy_applies_to_ordinary_work_and_is_reversible(registered):
    case, q, _, _ = registered
    standard = agent(q)
    set_focus = call("configure", arm="focused", expected=0)
    chosen = agent(q, "ordinary")
    assert chosen != standard
    assert library.execute(dict(project="Survey", action="search", query=q)) == chosen
    assert call("configure", arm="focused", expected=0) == set_focus
    assert not call("list", project="Other")["policy"]["focused"]
    call("arm", case=case, arm="current")
    assert agent(q, "control") == standard
    assert agent(q, "ordinary") == chosen
    with pytest.raises(ValueError, match="changed"):
        call("configure", arm="current", expected=0)
    result = call("configure", arm="current", expected=set_focus["id"])
    assert result["version"] is None and agent(q, "ordinary") == standard
    assert len(call("list")["policy_history"]) == 2


def test_a_new_version_needs_a_new_project_choice(registered, monkeypatch):
    case, q, _, _ = registered
    standard = agent(q)
    state = call("configure", arm="focused", expected=0)
    call("arm", case=case, arm="focused")
    monkeypatch.setattr(search_focus, "VERSION", "future-policy")
    assert agent(q, "normal") == standard
    checked = call("list")
    assert (
        not checked["policy"]["focused"]
        and checked["policy"]["version"] == state["version"]
    )
    assert not checked["window"]


def test_sessionless_tool_does_not_borrow_a_process_session(registered, monkeypatch):
    from gateway import session_context

    case, q, _, _ = registered
    monkeypatch.setenv("HERMES_SESSION_ID", "borrowed-from-elsewhere")
    monkeypatch.setattr(session_context, "bound_session_key", lambda: "")
    call("arm", case=case, arm="focused")
    default = json.loads(plugin.run(dict(project="Survey", action="search", query=q)))
    assert default == agent(q) and call("list")["window"]["used"] == 0


def test_required_receipt_is_rechecked_on_scoring(registered):
    case, _, roster, _ = registered
    with library.connection() as db:
        db.execute(
            "UPDATE editions SET body=body || 'edited' WHERE id=?",
            (roster[0]["edition"],),
        )
    with pytest.raises(ValueError, match="receipt"):
        call("replay", case=case)


def test_detail_fetch_only_returns_pairs_for_selected_case(registered):
    case, q, roster, _ = registered
    call("replay", case=case)
    newer = call("freeze", title="Different task", question=q, judgments=roster)["case"]
    call("replay", case=newer)
    for choice in (case, newer):
        r = call("list", case=choice)
        assert [k["id"] for k in r["cases"] if k["pairs"]] == [choice]


def test_quantity_extension_preserves_edition_and_budget():
    from hermes_cli.theseus_research import search_focus as f

    with library.connection() as db:
        rows = []
        for i in range(12):
            anchor = f"Operating condition {i} measured −0"
            body = (
                "Header. "
                + anchor
                + ".42 °C after equilibration in the reference bath only. Longer observations follow."
            )
            saved = library.capture(db, "Survey", dict(title=str(i), text=body))
            rows.append(
                dict(
                    edition=saved["edition"],
                    digest=saved["digest"],
                    short_text=None,
                    excerpt="…" + anchor + "…",
                )
            )
        before = [s["excerpt"] for s in rows]
        f.finish_local_quantities(db, rows)
        added = sum(
            len(s["excerpt"].encode()) - len(old.encode())
            for s, old in zip(rows, before, strict=True)
        )
        assert 0 < added <= f.EXTRA_BYTES
        assert (
            "−0.42 °C after equilibration in the reference bath only."
            in rows[0]["excerpt"]
        )
        assert rows[-1]["excerpt"] == before[-1]
        assert (
            sum(s["excerpt"] != old for s, old in zip(rows, before, strict=True))
            <= f.MAX_SOURCE_CHECKS
        )


@pytest.mark.parametrize(
    "body,snippet",
    [
        ("quantity is 3.8 mm. Again quantity is 3.9 mm.", "…quantity is 3…"),
        ("quantity is 3.8 mm.", "…unlocated 3…"),
        ("nothing quantitative here.", "…nothing quantitative…"),
        ("quantity is 3.8 mm " + ("long text " * 30) + ".", "…quantity is 3…"),
    ],
)
def test_ambiguous_unlocated_or_unbounded_text_is_left_alone(body, snippet):
    with library.connection() as db:
        s = library.capture(db, "Survey", dict(title="Control", text=body))
        row = dict(
            edition=s["edition"], digest=s["digest"], short_text=None, excerpt=snippet
        )
        search_focus.finish_local_quantities(db, [row])
        assert row["excerpt"] == snippet and "excerpt_start" not in row


def test_modified_local_quantity_is_not_returned():
    with library.connection() as db:
        s = library.capture(
            db, "Survey", dict(title="Control", text="quantity is 3.8 mm.")
        )
        row = dict(
            edition=s["edition"],
            digest="bad digest",
            short_text=None,
            excerpt="…quantity is 3…",
        )
        search_focus.finish_local_quantities(db, [row])
        assert (
            row["excerpt"] == "" and row["evidence_scope"] == "edition receipt mismatch"
        )


def test_multiple_required_passages_from_one_edition_are_independently_scored():
    opening = "Illustrative worksheet only."
    figure = "Fenwick courier rate is 43 per stop."
    body = (
        opening
        + "\n"
        + ("Dispatch index. " * 130)
        + "\n"
        + figure
        + "\n"
        + "Archive. " * 40
    )
    s = note("Route", body, project="Shelf")
    library.execute(
        dict(
            project="Survey",
            action="discovery_pin",
            origin_project="Shelf",
            source=s["source"],
            edition=s["edition"],
        )
    )
    roster = [dict(edition=s["edition"], role="required", passages=[opening, figure])]
    c = call(
        "freeze", title="Both claims", question="Fenwick courier rate", judgments=roster
    )["case"]
    r = call("replay", case=c)["results"]
    assert (r["current"]["passages"], r["current"]["required"]) == (1, 2)
    assert (r["focused"]["passages"], r["focused"]["required"]) == (2, 2)
    assert r["focused"]["sources"][0]["passages_returned"] == 2
    assert r["current"]["missing"] == [s["edition"]]
    for quotes in ([opening, opening], [opening, "absent"], ["x"] * 9, []):
        with pytest.raises(ValueError):
            call(
                "freeze",
                title="Invalid",
                question="Fenwick",
                judgments=[
                    dict(edition=s["edition"], role="required", passages=quotes)
                ],
            )


def test_extra_passages_never_displace_opening_and_obey_both_limits():
    body = "μ" * 210
    rows = [
        dict(
            edition=i,
            excerpt="Source restriction",
            evidence_scope="excerpt only",
            _matched=(350, body),
        )
        for i in range(8)
    ]
    status = search_focus.attach_passages(rows)
    assert (
        status["additional_passage_bytes"] == 2 * len(body.encode())
        and status["deferred_passages"] == 6
    )
    assert all(
        r["excerpt"] == "Source restriction" and "_matched" not in r for r in rows
    )
    assert search_focus.matched_text(rows[0], " " * 350 + body) == body
    assert not search_focus.matched_text(rows[0], " " * 351 + body)
    rows[0]["matched_passage"]["sha256"] = "incorrect"
    assert not search_focus.matched_text(rows[0])
    rows = [
        dict(
            excerpt="Header",
            evidence_scope="excerpt only",
            _matched=(90, "short passage"),
        )
        for _ in range(9)
    ]
    assert (
        search_focus.attach_passages(rows)["deferred_passages"]
        == 9 - search_focus.MAX_SUPPLEMENTS
    )


def test_deferred_matches_are_not_counted_as_evidence():
    rows = [
        dict(
            edition=i,
            source=i,
            title=str(i),
            digest=str(i),
            excerpt="Header",
            evidence_scope="excerpt only",
            _matched=(221, "target"),
        )
        for i in range(6)
    ]
    status = search_focus.attach_passages(rows)
    reply = {"coverage": {"extent": "partial source text", **status}, "sources": rows}
    score = search_trials.summarize(
        reply, {str(i): dict(role="required", quote="target") for i in range(6)}
    )
    assert (
        score["passages"] == 4 and score["required"] == 6 and score["missing"] == [4, 5]
    )


def test_second_excerpt_adds_query_terms_absent_from_the_opening():
    heading = (
        "Booking confirmation status provisional. Please do not assume a reservation."
    )
    late = "Cairn pottery workshop start is Thursday evening."
    body = (
        heading
        + "\n"
        + "Archive page. " * 160
        + "\n"
        + late
        + "\n"
        + "Record closed. " * 45
    )
    s = note("Community calendar", body, project="Shelf")
    library.execute(
        dict(
            action="discovery_pin",
            project="Survey",
            origin_project="Shelf",
            source=s["source"],
            edition=s["edition"],
        )
    )
    call("configure", arm="focused", expected=0)
    reply = agent(
        "Booking confirmation status reservation Cairn pottery workshop start",
        "regular",
    )
    [stored] = reply["sources"]
    assert heading in stored["excerpt"] and late in search_focus.matched_text(
        stored, body
    )
    assert stored["evidence_scope"] == "excerpt only"


def test_model_tool_cannot_enroll_or_change_a_project_policy(registered):
    for action in (
        "search_trial_arm",
        "search_trial_configure",
        "search_trial_scan_exports",
    ):
        answer = json.loads(
            plugin.run(
                dict(action=action, project="Survey", arm="focused", expected=0),
                session_id="s",
            )
        )
        assert "error" in answer
    assert not call("list")["policy"]["focused"] and not call("list")["window"]
