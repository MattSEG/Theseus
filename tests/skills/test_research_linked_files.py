"""Local file links are scoped to an explicit picker selection and an exact source edition."""

import hashlib
import pytest
from hermes_cli.theseus_research import library, plugin


def selected(path, allow=True, project="Measurements", body=None, digest=None):
    data = path.read_bytes() if body is None else body
    return plugin.desktop_request(
        dict(
            action="capture_selected_file",
            project=project,
            origin=str(path),
            text=data.decode("utf-8"),
            allow_reimport=allow,
            file_sha256=digest or hashlib.sha256(data).hexdigest(),
        )
    )


def refresh(old, project="Measurements"):
    return library.execute(
        dict(
            action="refresh_file",
            project=project,
            source=old["source"],
            expected_edition=old["edition"],
        ),
        resolve_tool_reads=True,
    )


def test_exact_bytes_after_atomic_replace_and_idempotent_current_read(tmp_path):
    f = tmp_path / "Terms.csv"
    first = b"\xef\xbb\xbfItem,Fee\r\nHand,45\r\n"
    f.write_bytes(first)
    old = selected(f)
    assert old["verified_file"] == dict(
        sha256=hashlib.sha256(first).hexdigest(), bytes=len(first)
    )
    assert old["file_linked"]
    alt = f.with_suffix(".pending")
    second = first.replace(b"45", b"58")
    alt.write_bytes(second)
    alt.replace(f)
    new = refresh(old)
    assert new["verified_file"] == dict(
        sha256=hashlib.sha256(second).hexdigest(), bytes=len(second)
    )
    assert refresh(new)["edition"] == new["edition"]
    assert new["edition"] != old["edition"]
    with pytest.raises(ValueError, match="changed"):
        refresh(old)
    with library.connection() as db:
        assert (
            db.execute("select body from editions where id=?", (old["edition"],))
            .fetchone()[0]
            .encode()
            == first
        )
        assert (
            db.execute("select body from editions where id=?", (new["edition"],))
            .fetchone()[0]
            .encode()
            == second
        )


def test_linked_agent_capture_uses_confirmed_file_and_unlinked_is_honest(tmp_path):
    f = tmp_path / "Work.md"
    f.write_bytes(b"Work log\r\n")
    old = selected(f)
    f.write_bytes(b"New entry.\r\n")
    args = dict(
        project="Measurements",
        action="capture",
        kind="file",
        origin=str(f),
        title=f.name,
        text="New entry.",
    )
    result = library.execute(args, resolve_tool_reads=True)
    assert (
        result["file_linked"]
        and result["verified_file"]["sha256"]
        == hashlib.sha256(f.read_bytes()).hexdigest()
    )
    search = lambda p: library.execute(dict(project=p, action="search", query="Work"))[
        "sources"
    ]
    assert search("Measurements")[0]["file_linked"]
    unlinked = library.execute(dict(args, project="Other"), resolve_tool_reads=True)
    assert (
        "verified_file" not in unlinked
        and "Submitted text" in unlinked["file_provenance"]
    )
    assert not search("Other")[0].get("file_linked")
    with pytest.raises(ValueError, match="no file link"):
        refresh(old, "Other")


def test_selection_and_revocation_never_grant_project_wide_access(tmp_path):
    f = tmp_path / "Sample.txt"
    f.write_text("A")
    old = selected(f, allow=False)
    assert not old["file_linked"]
    with pytest.raises(ValueError, match="no file link"):
        refresh(old)
    old = selected(f)

    def ui(action, **args):
        return plugin.desktop_request(
            dict(action=action, project="Measurements", **args)
        )

    (link,) = ui("file_list")["files"]
    with pytest.raises(ValueError, match="changed"):
        ui("file_revoke", source=old["source"], link=link["link"] + 1)
    ui("file_revoke", source=old["source"], link=link["link"])
    assert not ui("file_list")["files"]
    with pytest.raises(ValueError, match="no file link"):
        refresh(old)
    selected(f)
    with pytest.raises(ValueError, match="changed"):
        ui("file_revoke", source=old["source"], link=link["link"])


def test_rejected_drift_and_symlinks_do_not_replace_approved_edition(tmp_path):
    folder = tmp_path / "study"
    folder.mkdir()
    f = folder / "Study.csv"
    f.write_bytes(b"Value\n9\n")
    with pytest.raises(ValueError, match="differs"):
        selected(f, digest="0" * 64)
    old = selected(f)
    real = tmp_path / "Different.txt"
    real.write_text("Other data")
    f.unlink()
    f.symlink_to(real)
    with pytest.raises((OSError, ValueError)):
        refresh(old)
    f.unlink()
    f.hardlink_to(real)
    with pytest.raises(ValueError, match="single filesystem link"):
        refresh(old)
    folder.rename(tmp_path / "former")
    folder.mkdir()
    f.write_text("Different folder")
    with pytest.raises(ValueError, match="replaced"):
        refresh(old)
    assert (
        library.execute(
            dict(action="read", project="Measurements", source=old["source"])
        )["edition"]
        == old["edition"]
    )


def test_invalid_or_oversized_byte_input_does_not_partially_save(tmp_path):
    f = tmp_path / "Terms.tsv"
    f.write_bytes(b"Initial text\n")
    old = selected(f)
    for data in (b"\xff\xff", b"x" * (library.MAX_TEXT + 1)):
        f.write_bytes(data)
        with pytest.raises(ValueError):
            refresh(old)
    read = library.execute(
        dict(project="Measurements", action="read", source=old["source"])
    )
    assert read["edition"] == old["edition"] and read["text"] == "Initial text\n"


def test_model_cannot_grant_itself_a_file_link(tmp_path):
    f = tmp_path / "Test.csv"
    f.write_text("Subject,Cost\nTest,20\n")
    params = dict(
        action="capture_selected_file",
        project="Other",
        origin=str(f),
        text=f.read_text(),
        file_sha256=hashlib.sha256(f.read_bytes()).hexdigest(),
        allow_reimport=True,
    )
    with pytest.raises(ValueError):
        library.execute(params, resolve_tool_reads=True)
    params.update(action="capture", kind="file", title="Transcription")
    result = library.execute(params, resolve_tool_reads=True)
    with pytest.raises(ValueError, match="no file link"):
        refresh(result, "Other")


def test_archive_proof_matches_the_exact_edition_and_does_not_read_disk_on_lookup(
    tmp_path,
):
    f = tmp_path / "Cost.csv"
    f.write_bytes(b"Item,Value\r\nWork,45\r\n")
    verified = selected(f)
    f.write_bytes(b"New on disk, deliberately not reimported\n")
    r = library.execute(dict(action="search", project="Measurements", query="Cost"))[
        "sources"
    ][0]
    assert r["file_proof"]["status"] == "verified at import"
    assert (
        r["file_proof"]["sha256"] == verified["digest"]
        and r["file_proof"]["bytes"] != f.stat().st_size
    )
    plugin.desktop_request(
        dict(
            action="file_revoke",
            project="Measurements",
            source=verified["source"],
            link=plugin.desktop_request(
                dict(action="file_list", project="Measurements")
            )["files"][0]["link"],
        )
    )
    copy = library.execute(
        dict(
            action="capture",
            project="Measurements",
            origin=str(f),
            title="Cost.csv",
            kind="file",
            text="Copied text",
        ),
        resolve_tool_reads=True,
    )
    read = lambda e: library.execute(
        dict(action="read", project="Measurements", edition=e)
    )
    assert read(copy["edition"])["file_proof"]["status"] == "archive only"
    assert read(verified["edition"])["file_proof"]["status"] == "verified at import"
