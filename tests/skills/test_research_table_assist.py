import pytest
from hermes_cli.theseus_research import library, plugin, table_assist

TABLE = "Route,Elapsed (min),Ticket ID\nNorth,12,00441\nNorth,16,00442\nNorth,ND,00443\nSouth,21,00444\nSouth,19,00445\n"


def op(action, **args):
    return library.execute(dict(action=action, project="Timing", **args))


def ui(action, **args):
    return plugin.desktop_request(dict(action=action, project="Timing", **args))


def capture(text):
    return op(
        "capture",
        kind="file",
        origin="/selected/route.csv",
        title="Run timing",
        text=text,
    )


def test_preview_is_read_only_and_leads_to_the_same_saved_analysis():
    reply = capture(TABLE)
    hint = reply["analysis_suggestion"]
    args = dict(source=hint["source"], edition=hint["edition"], sha256=hint["sha256"])
    assert (
        hint["measure"] == "Elapsed (min)"
        and hint["group"] == "Route"
        and hint["unit"] == "min"
    )
    assert not op("analysis_list")["runs"]
    page = op("analysis_preview", **args)
    assert [
        (
            r["group"],
            r["n"],
            r["minimum"],
            r["maximum"],
            r["min_record"],
            r["max_record"],
        )
        for r in page["groups"]
    ] == [("North", 2, "12", "16", 2, 3), ("South", 2, "19", "21", 6, 5)]
    assert page["excluded_count"] == 1 and page["excluded"][0]["record"] == 4
    assert not op("analysis_list")["runs"]
    kept = ui("analysis_keep_preview", **args)
    assert ui("analysis_keep_preview", **args) == kept
    (saved,) = op("analysis_list")["runs"]
    assert saved["id"] == kept["run"] and saved["report"]["digest"] == hint["sha256"]
    assert op("analysis_record", run=kept["run"], record=5)["values"] == [
        "South",
        "21",
        "00444",
    ]


def test_stale_preview_cannot_be_saved_or_read_as_current():
    first = capture(TABLE)
    args = dict(
        source=first["source"], edition=first["edition"], sha256=first["digest"]
    )
    capture(TABLE.replace(",12,", ",14,"))
    with pytest.raises(ValueError, match="changed"):
        ui("analysis_keep_preview", **args)
    with pytest.raises(ValueError, match="changed"):
        op("analysis_preview", **args)
    assert not op("analysis_list")["runs"]


@pytest.mark.parametrize(
    "body",
    [
        TABLE.replace("Elapsed (min)", "Postal code"),
        TABLE.replace("Elapsed (min)", "Serial ID (min)"),
        TABLE.replace("Ticket ID", "Weight (kg)"),
        TABLE.replace("Route,", "Route,Shift,")
        .replace("North,", "North,Morning,")
        .replace("South,", "South,Evening,"),
        "Route,Value (USD),Currency\nA,5,USD\nA,9,EUR\nB,4,EUR\nB,2,USD\n",
        "Route,Mass (g)\nA,1\nA,2\nB,ND\nB,3\n",
        "Route,Mass (g)\nA,1\nA,2\n",
        "Route,Mass (g)\n" + ("A,4\nB,5\n" * 525),
    ],
)
def test_declines_ambiguous_dimensions_codes_units_or_insufficient_data(body):
    assert table_assist.candidate(body) is None


def test_projects_can_disable_table_suggestions():
    assert ui("analysis_suggestions") == {"enabled": True}
    ui("analysis_suggestions", expected=True, enabled=False)
    assert "analysis_suggestion" not in capture(TABLE)
    assert plugin.desktop_request(
        dict(action="analysis_suggestions", project="Other")
    ) == {"enabled": True}
    with pytest.raises(ValueError):
        ui("analysis_suggestions", expected=True, enabled=True)
