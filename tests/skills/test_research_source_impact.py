"""Real brief editions drive suggestions at import time, including pre-index briefs."""

import pytest
from hermes_cli.theseus_research import library, plugin, source_impact

PROJECT = "Print quotes"
BEFORE = "Item,Price,Scope\nRun,47 USD,500 copies\nCard,16 USD,100 copies\nBinding,8 USD,per book\n"
AFTER = BEFORE.replace("47 USD", "52 USD").replace("100 copies", "50 copies")


def op(action, project=PROJECT, **fields):
    return library.execute(dict(action=action, project=project, **fields))


def ui(action, project=PROJECT, **fields):
    return plugin.desktop_request(
        dict(action="impact_" + action, project=project, **fields)
    )


def capture(text, project=PROJECT, origin="/quotes/vendor.csv"):
    return op("capture", project, kind="file", origin=origin, title="Vendor", text=text)


def brief(edition, name="Print budget", project=PROJECT):
    return op(
        "brief_create",
        project,
        title=name,
        question="What will printing cost?",
        columns=["Price"],
        rows=[
            dict(
                name=record.split(",")[0],
                cells={
                    "Price": dict(
                        value=record.split(",")[1], edition=edition, quote=record
                    )
                },
            )
            for record in BEFORE.splitlines()[1:]
        ],
    )


def test_material_field_and_context_changes_surface_on_import_only():
    old = capture(BEFORE)
    doc = brief(old["edition"])
    assert "affected_briefs" not in capture(BEFORE)
    result = capture(AFTER)
    impact = result["affected_briefs"]
    assert impact["checked"] == 3 and impact["digest"] == result["digest"]
    changed, scope = impact["items"]
    assert (changed["row"], changed["prior"], changed["value"], changed["state"]) == (
        "Run",
        "47 USD",
        "52 USD",
        "changed",
    )
    assert scope["state"] == "context changed" and scope["context_changes"] == [
        dict(field="Scope", before="100 copies", after="50 copies")
    ]
    assert op("brief_read", brief=doc["brief"]) == doc
    args = dict(
        brief=doc["brief"],
        expected_revision=doc["revision"],
        new_edition=result["edition"],
        column="Price",
    )
    with pytest.raises(ValueError, match="context"):
        op("refresh_apply", row="Card", **args)
    op("refresh_apply", row="Run", **args)
    assert [
        c["row"]
        for c in ui("check", source=result["source"], edition=result["edition"])[
            "items"
        ]
    ] == ["Card"]
    assert op("brief_read", brief=doc["brief"], revision=1) == {**doc, "latest": 2}
    op(
        "refresh_keep",
        brief=doc["brief"],
        row="Card",
        column="Price",
        new_edition=result["edition"],
        expected_revision=2,
        reason="Retain the original run size",
    )
    assert not ui("check", source=result["source"], edition=result["edition"])["items"]


def test_modes_and_duplicate_imports():
    old = capture(BEFORE)
    brief(old["edition"])
    ui("mode", mode="suggest", expected="automatic")
    updated = capture(AFTER)
    assert (
        updated["affected_briefs"]["offered"]
        and updated["affected_briefs"]["checked"] == 0
    )
    assert ui("check", source=old["source"], edition=updated["edition"])["checked"] == 3
    assert not capture(AFTER).get("affected_briefs")
    ui("dismiss", source=old["source"], edition=updated["edition"])
    assert ui("recent")["items"] == []
    ui("mode", mode="off", expected="suggest")
    assert not capture(AFTER.replace("52", "54")).get("affected_briefs")
    assert ui("mode", "Elsewhere") == {"mode": "automatic"}
    with pytest.raises(ValueError):
        ui("mode", mode="automatic", expected="suggest")


def test_pin_does_not_follow_owner_and_rollback_cancels_notice():
    old = capture(BEFORE)
    brief(old["edition"])
    op(
        "discovery_pin",
        "Pinned",
        source=old["source"],
        edition=old["edition"],
        origin_project=PROJECT,
    )
    brief(old["edition"], project="Pinned")
    revised = capture(AFTER)
    assert ui("recent", "Pinned")["items"] == []
    with pytest.raises(ValueError, match="changed again"):
        ui("check", "Pinned", source=old["source"], edition=revised["edition"])
    with pytest.raises(ValueError, match="matching source"):
        ui("check", "Elsewhere", source=old["source"], edition=revised["edition"])
    assert not capture(BEFORE).get("affected_briefs")
    assert ui("recent")["items"] == []


def test_backfills_old_briefs_and_pages_local_citations(monkeypatch):
    old = capture(BEFORE)
    for i in range(4):
        brief(old["edition"], "Printing " + str(i))
    with library.connection() as db:
        db.execute("DELETE FROM brief_citation_index")
        db.execute("DELETE FROM brief_citation_cells")
    monkeypatch.setattr(source_impact, "PAGE", 2)
    revised = capture(AFTER)
    assert revised["affected_briefs"]["pending_index"]
    args = dict(source=old["source"], edition=revised["edition"])
    page = ui("check", **args)
    assert not page["pending_index"]
    seen = set()
    while True:
        for cell in page["items"]:
            key = (cell["brief"], cell["row"])
            assert key not in seen
            seen.add(key)
        if not page["next_after"]:
            break
        page = ui("check", **args, after=page["next_after"])
    assert len(seen) == 8


def test_unchanged_records_and_uncited_sources_are_quiet():
    a = capture(BEFORE)
    brief(a["edition"])
    assert "affected_briefs" not in capture(BEFORE + "Extra,90 USD,one case\n")
    capture("Header\nFirst\n", origin="/quotes/other.csv")
    assert "affected_briefs" not in capture(
        "Header\nSecond\n", origin="/quotes/other.csv"
    )
    assert not ui("recent")["items"]


def test_revised_notes_distinguish_nearby_qualification_from_distant_edit():
    text = (
        "Valid for retail only. The refund window is 12 days. " + "Padding words. " * 48
    )
    args = dict(kind="file", origin="/terms/returns.txt", title="Returns")
    old = op("capture", text=text, **args)
    op(
        "brief_create",
        title="Returns decision",
        question="When?",
        columns=["Window"],
        rows=[
            dict(
                name="Retail",
                cells={
                    "Window": dict(
                        value="12 days",
                        quote="The refund window is 12 days.",
                        edition=old["edition"],
                    )
                },
            )
        ],
    )
    assert "affected_briefs" not in op("capture", text=text + "Footer.", **args)
    changed = op(
        "capture", text=text.replace("retail only", "retail and wholesale"), **args
    )["affected_briefs"]
    assert changed["items"][0]["state"] == "nearby wording changed"
    assert changed["items"][0]["value"] is None


def test_only_explicit_adoption_surfaces_the_borrowing_projects_changes():
    old = capture(BEFORE)
    guest = "Studio order"
    op("discovery_pin", guest, source=old["source"], edition=old["edition"], origin_project=PROJECT)
    prior = brief(old["edition"], project=guest)
    new = capture(AFTER)
    assert not ui("recent", guest)["items"]
    args = dict(source=old["source"], previous=old["edition"], chosen=new["edition"], revision=0)
    adopted = op("discovery_adopt", guest, **args)
    impact = adopted["affected_briefs"]
    assert {c["brief"] for c in impact["items"]} == {prior["brief"]}
    assert {c["state"] for c in impact["items"]} == {"changed", "context changed"}
    assert "affected_briefs" not in op("discovery_adopt", guest, **args)
    assert op("brief_read", guest, brief=prior["brief"]) == prior
