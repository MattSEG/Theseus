import importlib.util
from pathlib import Path

from PIL import Image
import pytest


SOURCE = Path(__file__).resolve().parents[2] / "skills/software-development/learning-run/scripts/vision.py"
spec = importlib.util.spec_from_file_location("learning_vision", SOURCE)
vision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vision)


@pytest.fixture(autouse=True)
def isolated_artifact_roots(tmp_path,monkeypatch):
    # The shared artifact reader checks hard links against profile-specific paths.
    monkeypatch.setenv("HOME",str(tmp_path))
    monkeypatch.setenv("CODEX_HOME",str(tmp_path/".codex"))


def test_metrics_use_original_viewport_coordinates():
    a = Image.new("RGB", (20, 30), "white")
    b = a.copy()
    b.putpixel((7, 9), (254, 255, 255))
    metrics, *_ = vision.compare(a, b, (5, 6, 15, 16))
    assert metrics["changed_pixels"] == 1 and metrics["changed_box"] == [7, 9, 8, 10]
    assert metrics["compared_pixels"] == 100
    assert vision.compare(a, b, (0, 0, 2, 2))[0]["changed_pixels"] == 0
    with pytest.raises(ValueError, match="region"):
        vision.compare(a, b, (-1, 0, 5, 5))
    with pytest.raises(ValueError, match="sizes differ"):
        vision.compare(a, b.crop((0, 0, 2, 2)))


def test_interpretation_requires_selected_image():
    path = Path("/tmp/pair.png")
    start = {"name": "vision_analyze", "type": "tool_use", "input": {"image_url": str(path)}, "tool_call_id": "a"}
    finish = {"name": "vision_analyze", "type": "tool_result", "output": "Image loaded into your context", "tool_call_id": "a"}
    assert vision.verified_vision_result([start, finish], path)
    assert not vision.verified_vision_result([finish], path)
    assert not vision.verified_vision_result([start, dict(finish, is_error=True)], path)
    assert not vision.verified_vision_result([start, finish], Path("/tmp/different.png"))


def test_receipt_recomputes_current_and_legacy_comparison(tmp_path, monkeypatch, capsys):
    import json
    before, after = (tmp_path / name for name in ("before.png", "after.png"))
    Image.new("RGB", (16, 12), "blue").save(before)
    Image.new("RGB", (16, 12), "red").save(after)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["vision", str(before), str(after)])
    vision.main()
    receipt = Path(json.loads(capsys.readouterr().out)["receipt"])
    assert vision.check_receipt(receipt) == "current"
    data = json.loads(receipt.read_text())
    del data["comparison_sha256"]
    receipt.write_text(json.dumps(data))
    assert vision.check_receipt(receipt) == "current"
    Image.new("RGB", (40, 42), "grey").save(receipt.parent / "comparison.png")
    assert vision.check_receipt(receipt) == "comparison pixels differ"
    Image.new("RGB", (16, 12), "green").save(after)
    assert vision.check_receipt(receipt) == "input capture changed"


def test_location_cells_are_bounded_and_cover_corner_edges():
    assert vision.cell_box((600, 800), "A1") == (0, 0, 100, 100)
    assert vision.cell_box((600, 800), "F8", padding=True) == (400, 650, 600, 800)
    assert vision.cell_box((601, 801), "F8")[2:] == (601, 801)
    with pytest.raises(ValueError):
        vision.cell_box((600, 800), "G0")


def test_location_receipt_checks_pixels_and_verdict_without_model(tmp_path, monkeypatch, capsys):
    import json

    frame = tmp_path / "capture.png"
    Image.new("RGB", (60, 80), "blue").save(frame)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    answers = iter(["C4", "YES\nPrint proof"])
    monkeypatch.setattr(vision, "ask_image", lambda *args: {
        "session": "fixture", "image_inspected": True, "answer": next(answers)})
    vision.locate_capture(frame, "Print proof")
    receipt = Path(json.loads(capsys.readouterr().out)["receipt"])
    original = json.loads(receipt.read_text())
    assert vision.check_receipt(receipt) == "current"
    for changes, expected in [
        ({"inspected_box": [-1, 0, 59, 79]}, "invalid crop coordinates"),
        ({"cell": "D4"}, "location cell differs"),
        ({"state": "unverified"}, "location result inconsistent"),
        ({"inspection": None}, "crop inspection missing"),
    ]:
        receipt.write_text(json.dumps(original | changes))
        assert vision.check_receipt(receipt) == expected
    receipt.write_text(json.dumps(original))
    crop = receipt.parent / "crop.png"
    with Image.open(crop) as im:
        changed = im.copy()
    changed.putpixel((0, 0), (255, 0, 0))
    changed.save(crop)
    assert vision.check_receipt(receipt) == "location crop pixels differ"
    Image.new("RGB", (60, 80), "red").save(frame)
    assert vision.check_receipt(receipt) == "input capture changed"


def test_expected_pixels_control_exit_and_replay(tmp_path, monkeypatch, capsys):
    import json

    before, after = (tmp_path / n for n in ("before.png", "after.png"))
    im = Image.new("RGB", (12, 12), "white")
    im.save(before)
    im.putpixel((8, 8), (12, 34, 56))
    im.save(after)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for region, expected, code in [
        ((0, 0, 4, 4), "unchanged", 0), ((0, 0, 4, 4), "changed", 2),
        ((7, 7, 10, 10), "changed", 0), ((7, 7, 10, 10), "unchanged", 2),
    ]:
        monkeypatch.setattr("sys.argv", ["vision", str(before), str(after), "--region", *map(str, region),
                                        "--expect", expected])
        assert vision.main() == code
        result = json.loads(capsys.readouterr().out)
        assert result["assertion"]["passed"] is (code == 0)
        receipt = Path(result["receipt"])
        assert vision.check_receipt(receipt) == "current"
    content = json.loads(receipt.read_text())
    content["assertion"]["passed"] = True
    receipt.write_text(json.dumps(content))
    assert vision.check_receipt(receipt) == "recorded pixel assertion differs"


def test_exact_label_receipt_preserves_ambiguity_and_rechecks_boxes(tmp_path, monkeypatch, capsys):
    import json
    import subprocess

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(vision.sys, "platform", "darwin")
    frame = tmp_path / "input.png"
    Image.new("RGB", (60, 80), "white").save(frame)
    boxes = [[2, 3, 18, 11], [2, 35, 20, 44]]
    def recognize(command, **kwargs):
        assert command[-1] == "Review proof" and kwargs["input"].startswith(b"\x89PNG")
        return subprocess.CompletedProcess(command, 0, json.dumps({"boxes": boxes, "recognizer_revision": 3}).encode())
    monkeypatch.setattr(vision.subprocess, "run", recognize)
    vision.locate_exact_text(frame, "Review proof")
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ambiguous label" and result["label_boxes"] == boxes
    receipt = Path(result["receipt"])
    assert vision.check_receipt(receipt) == "current"
    boxes.pop()
    assert vision.check_receipt(receipt) == "recorded text locations differ"
    assert vision.exact_text_bounds(Image.new("RGB", (60, 80)), "Review proof")["state"] == "unique label"
    boxes.clear()
    assert vision.exact_text_bounds(Image.new("RGB", (60, 80)), "Review proof")["state"] == "not found"


def test_label_band_requires_unique_ordered_boundaries_and_whole_target(monkeypatch):
    import json
    import subprocess

    monkeypatch.setattr(vision.sys, "platform", "darwin")
    edges = [[[1, 5, 20, 12]], [[1, 40, 20, 48]]]
    found = [[1, 10, 16, 15], [1, 26, 18, 35], [1, 38, 19, 42]]
    def recognize(command, **kwargs):
        assert command[-3:] == ["Review", "Upper", "Lower"]
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "boxes": found, "boundary_boxes": edges, "recognizer_revision": 3}).encode())
    monkeypatch.setattr(vision.subprocess, "run", recognize)
    def locate():
        return vision.exact_text_bounds(Image.new("RGB", (60, 80)), "Review", ["Upper", "Lower"])
    assert locate()["label_boxes"] == [found[1]]
    found.append([2, 16, 14, 22])
    assert locate()["state"] == "ambiguous label"
    edges[0].append([1, 42, 18, 52])
    assert locate()["state"] == "unverified boundaries"
    edges[0].clear()
    assert locate()["state"] == "unverified boundaries"


def test_table_heading_intersection_requires_real_alignment_and_replays(tmp_path, monkeypatch, capsys):
    import json
    import subprocess

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(vision.sys, "platform", "darwin")
    frame = tmp_path / "table.png"
    Image.new("RGB", (200, 100), "white").save(frame)
    row = [[4, 55, 25, 65]]
    column = [[110, 4, 140, 14]]
    target = [102, 55, 149, 67]
    boxes = [target, [50, 55, 90, 67], [102, 30, 149, 42], [102, 0, 149, 6]]

    def recognize(command, **kwargs):
        assert command[-3:] == ["Review", "Jackets", "Print"]
        payload = {"boxes": boxes, "boundary_boxes": [row, column], "recognizer_revision": 3}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode())

    monkeypatch.setattr(vision.subprocess, "run", recognize)
    def locate():
        vision.locate_exact_text(frame, "Review", intersection=["Jackets", "Print"])
        return json.loads(capsys.readouterr().out)

    result = locate()
    assert result["label_boxes"] == [target] and result["unscoped_match_count"] == 4
    receipt = Path(result["receipt"])
    assert vision.check_receipt(receipt) == "current"
    record = json.loads(receipt.read_text())
    record["at_intersection"] = None
    receipt.write_text(json.dumps(record))
    assert vision.check_receipt(receipt) == "recorded text scope incomplete"
    boxes.append([114, 56, 138, 65])
    assert locate()["state"] == "ambiguous label"
    row.append([1, 4, 15, 8])
    assert locate()["state"] == "unverified boundaries"
    row.clear()
    assert locate()["state"] == "unverified boundaries"
