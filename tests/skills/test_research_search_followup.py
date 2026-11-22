import pytest
from hermes_cli.theseus_research import search_followup as future


def cell(
    source=1,
    body=None,
    quote=None,
    current=False,
    focused=True,
    read=True,
    original=None,
):
    return dict(
        row="Finding",
        column="Value",
        title="Test source",
        source=source,
        edition=source,
        digest=body or "body" + str(source),
        quote_digest=quote or "quote" + str(source),
        current=True,
        original_read=read,
        search=dict(
            current=current,
            focused=focused,
            original=focused if original is None else original,
        ),
    )


def test_distinguishes_support_loss_and_tie_using_observed_tool_output():
    baseline = future.keys(cell())

    def grade(*cells, arm="focused", extra=None):
        return future.grade(
            dict(cells=cells, source_reads=1, other_authoring_tools=extra or []),
            arm,
            baseline,
        )

    assert grade(cell(2))[:2] == ("supported", True)
    assert grade(cell(2, current=True, focused=False))[:2] == ("counterexample", True)
    assert grade(cell(2, current=True))[:2] == ("no difference", True)
    assert grade(cell(2), cell(1, current=True, focused=False))[:2] == (
        "counterexample",
        True,
    )
    assert grade(cell(2, original=False), arm="current")[:2] == ("counterexample", True)
    assert grade(cell(2), extra=["read_file"])[:2] == ("unverified", False)
    assert grade(cell(2, original=False))[:2] == ("unverified", False)
    assert grade(cell(2, current=False, focused=False, read=False))[:2] == (
        "unverified",
        False,
    )


@pytest.mark.parametrize("c", [cell(1), cell(2, body="body1"), cell(3, quote="quote1")])
def test_duplicates_do_not_consume_transfer_slots(c):
    kind, counted, proof = future.grade(
        dict(cells=[c], source_reads=2), "focused", future.keys(cell())
    )
    assert (kind, counted) == ("repeated evidence", False)
    assert proof["reads"] == 2 and not proof["gained"]


def test_an_unverified_run_still_exposes_the_cited_material():
    observed = dict(cells=[cell(5)], source_reads=1)
    failed, eligible, proof = future.grade(
        dict(observed, other_authoring_tools=["research_brief"]), "focused", set()
    )
    assert failed == "unverified" and not eligible
    exposed = set(proof["source_keys"])
    assert future.grade(observed, "focused", exposed)[:2] == ("repeated evidence", False)
    assert future.grade(dict(cells=[cell(6)]), "focused", exposed)[:2] == ("supported", True)
