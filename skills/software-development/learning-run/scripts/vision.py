"""Compare two operator-selected Desktop captures. Pixel metrics precede interpretation."""
import argparse
import hashlib
import io
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

from hermes_cli.artifact_inputs import open_artifact
from PIL import Image, ImageChops, ImageDraw


def load_png(path, max_pixels=16_000_000):
    with open_artifact(path) as fh:
        digest = hashlib.file_digest(fh, "sha256").hexdigest()
        fh.seek(0)
        with Image.open(fh) as picture:
            if picture.format != "PNG" or picture.width * picture.height > max_pixels:
                raise ValueError("use PNG captures of at most 16 megapixels")
            rgba = picture.convert("RGBA")
    white = Image.new("RGBA", rgba.size, "white")
    return Image.alpha_composite(white, rgba).convert("RGB"), digest


def compare(before, after, region=None):
    if before.size != after.size:
        raise ValueError("viewport sizes differ; capture both at the same zoom and window size")
    w, h = before.size
    rect = tuple(region) if region else (0, 0, w, h)
    x0, y0, x1, y1 = rect
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise ValueError("region must be a non-empty box inside both captures")
    a, b = before.crop(rect), after.crop(rect)
    r, g, blue = ImageChops.difference(a, b).split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), blue).point(lambda p: 255 if p else 0)
    bbox = mask.getbbox()
    absolute = [bbox[0]+x0, bbox[1]+y0, bbox[2]+x0, bbox[3]+y0] if bbox else None
    return {"viewport": [w, h], "region": list(rect), "changed_pixels": mask.histogram()[255],
            "compared_pixels": a.width*a.height, "changed_box": absolute}, a, b


def verified_vision_result(events, image_path):
    # Ignore a plausible final answer when the requested PNG never reached the model.
    selected = set()
    for event in events:
        if event.get("name") != "vision_analyze":
            continue
        key = event.get("tool_call_id")
        if event.get("type") == "tool_use" and event.get("input", {}).get("image_url") == str(image_path):
            selected.add(key)
        if (event.get("type") == "tool_result" and key in selected
                and not event.get("is_error") and "Image loaded into your context" in event.get("output", "")):
            return True
    return False


def review_pair(launcher, image_path, question):
    prompt = ("Call vision_analyze on this local comparison PNG. Use its pixels to answer the question. "
              "The left panel is BEFORE and the right panel is AFTER. Identify the visible element, "
              "its position, and the evidence for the answer. Report uncertainty when unclear.\n"
              + str(image_path) + "\nQuestion: " + question)
    return ask_image(launcher, image_path, prompt)


def ask_image(launcher, image_path, prompt):
    proc = subprocess.run([str(launcher), "chat", "--oneshot", "--provider", "openai-codex",
                           "-m", "gpt-6.2-astra", "-t", "vision", "--max-turns", "4",
                           "--format", "stream-json", "--query-file", "-"],
                          input=prompt, capture_output=True, text=True, timeout=150, check=False)
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode or not events or events[-1].get("exit_code") != 0:
        raise ValueError("the visual review failed; the pixel comparison remains available")
    if not verified_vision_result(events, image_path):
        raise ValueError("the selected image could not be verified in the model context")
    return {"session": events[-1]["session_id"], "image_inspected": True, "answer": events[-1]["text"]}


def comparison_sheet(a, b):
    pair = Image.new("RGB", (a.width * 2 + 8, a.height + 30), "#eeeeee")
    pair.paste(a, (0, 30))
    pair.paste(b, (a.width + 8, 30))
    pen = ImageDraw.Draw(pair)
    pen.text((6, 6), "BEFORE", fill="black")
    pen.text((a.width + 14, 6), "AFTER", fill="black")
    pair.thumbnail((2400, 1800))
    return pair


def pixel_assertion(expected, changed_pixels):
    observed = "changed" if changed_pixels else "unchanged"
    return {"expected": expected, "observed": observed, "passed": observed == expected}


def check_receipt(receipt):
    with open_artifact(receipt) as stream:
        record = json.load(stream)
    if record.get("method") == "macos-exact-text":
        source, digest = load_png(Path(record["source"]))
        if digest != record["source_sha256"]:
            return "input capture changed"
        scopes = {"between_labels", "at_intersection"}.intersection(record)
        details = {"boundary_boxes", "unscoped_match_count"}.intersection(record)
        if (scopes or details) and (len(scopes) != 1 or len(details) != 2 or any(record[k] is None for k in scopes)):
            return "recorded text scope incomplete"
        expected = exact_text_bounds(source, record["target"], record.get("between_labels"),
                                     intersection=record.get("at_intersection"))
        if any(record.get(k) != value for k, value in expected.items()):
            return "recorded text locations differ"
        return "current"
    if "source_sha256" in record:
        return check_location(record, receipt.parent)
    a, first = load_png(Path(record["before"]))
    b, second = load_png(Path(record["after"]))
    if (first, second) != (record["before_sha256"], record["after_sha256"]):
        return "input capture changed"
    metrics, a, b = compare(a, b, record["region"])
    if any(record.get(k) != value for k, value in metrics.items()):
        return "recorded pixel measurements differ"
    if "assertion" in record:
        check = record["assertion"]
        if not isinstance(check, dict) or check.get("expected") not in ("changed", "unchanged"):
            return "invalid pixel assertion"
        if check != pixel_assertion(check["expected"], metrics["changed_pixels"]):
            return "recorded pixel assertion differs"
    saved, digest = load_png(receipt.parent / "comparison.png")
    if "comparison_sha256" in record and record["comparison_sha256"] != digest:
        return "comparison file changed"
    sheet = comparison_sheet(a, b)
    if sheet.size != saved.size or ImageChops.difference(sheet, saved).getbbox():
        return "comparison pixels differ"
    return "current"


def cell_box(size, cell, padding=False):
    if not re.fullmatch("[A-F][1-8]", cell):
        raise ValueError("invalid location cell")
    col, row = ord(cell[0]) - ord("A"), int(cell[1]) - 1
    w, h = size
    dx, dy = (1, 0.5) if padding else (0, 0)
    return (max(0, int((col - dx) * w / 6)), max(0, int((row - dy) * h / 8)),
            min(w, int((col + 1 + dx) * w / 6)), min(h, int((row + 1 + dy) * h / 8)))


def grid_image(source):
    out = Image.new("RGB", (source.width + 32, source.height + 32), "white")
    out.paste(source, (32, 32))
    pen = ImageDraw.Draw(out)
    for col in range(6):
        x = 32 + col * source.width // 6
        pen.line((x, 32, x, out.height), fill="#88a5b5")
        pen.text((x + source.width // 12, 3), chr(65 + col), fill="black", font_size=20)
    for row in range(8):
        y = 32 + row * source.height // 8
        pen.line((32, y, out.width, y), fill="#88a5b5")
        pen.text((5, y + source.height // 16), str(row + 1), fill="black", font_size=20)
    return out


def check_location(record, folder):
    source, digest = load_png(Path(record["source"]))
    if digest != record["source_sha256"] or list(source.size) != record["viewport"]:
        return "input capture changed"

    def matches(name, expected):
        saved, _ = load_png(folder / name, expected.width * expected.height)
        return saved.size == expected.size and not ImageChops.difference(saved, expected).getbbox()

    if not matches("grid.png", grid_image(source)):
        return "location grid pixels differ"
    first = record["first"]
    if not isinstance(first, dict) or first.get("image_inspected") is not True or not isinstance(first.get("answer"), str):
        return "grid inspection missing"
    cell = first["answer"].strip().upper()
    if not re.fullmatch("[A-F][1-8]", cell):
        expected = "not found" if cell == "ABSENT" else "unverified"
        if record["state"] != expected or any(k in record for k in ("cell", "cell_box", "inspected_box", "inspection")):
            return "location result inconsistent"
        return "current"
    bounds = cell_box(source.size, cell)
    if record["cell"] != cell or record["cell_box"] != list(bounds):
        return "location cell differs"
    box = record["inspected_box"]
    if not isinstance(box, list) or len(box) != 4 or any(type(v) != int for v in box):
        return "invalid crop coordinates"
    x0, y0, x1, y1 = box
    if not (0 <= x0 <= bounds[0] < bounds[2] <= x1 <= source.width and
            0 <= y0 <= bounds[1] < bounds[3] <= y1 <= source.height):
        return "invalid crop coordinates"
    if not matches("crop.png", source.crop(box)):
        return "location crop pixels differ"
    inspection = record["inspection"]
    if not isinstance(inspection, dict) or inspection.get("image_inspected") is not True or not isinstance(inspection.get("answer"), str):
        return "crop inspection missing"
    yes = inspection["answer"].strip().splitlines()[:1] == ["YES"]
    if record["state"] != ("verified in crop" if yes else "unverified"):
        return "location result inconsistent"
    return "current"


def aligned_cell(box, row, column):
    # These tables have a row heading on the left and a column heading above.
    row_center = (row[1] + row[3]) / 2
    column_center = (column[0] + column[2]) / 2
    return (row[2] <= box[0] and column[3] <= box[1]
            and box[1] <= row_center < box[3] and box[0] <= column_center < box[2])


def exact_text_bounds(source, target, boundaries=None, *, intersection=None):
    if sys.platform != "darwin":
        raise ValueError("exact label location requires macOS Vision")
    if not isinstance(target, str) or not 1 <= len(target.strip()) <= 300:
        raise ValueError("supply one visible text label of up to 300 characters")
    if boundaries is not None and intersection is not None:
        raise ValueError("choose one exact-text scope")
    anchors = intersection if intersection is not None else boundaries
    if anchors is not None and (not isinstance(anchors, (list, tuple)) or len(anchors) != 2
            or any(not isinstance(label, str) or not 1 <= len(label.strip()) <= 300 for label in anchors)):
        raise ValueError("supply two visible anchor labels")
    data = io.BytesIO()
    source.save(data, format="PNG")
    helper = Path(__file__).with_name("label-bounds.swift")
    try:
        result = subprocess.run(["/usr/bin/xcrun", "swift", str(helper), target, *(anchors or [])], input=data.getvalue(),
                                capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("macOS text recognition timed out") from exc
    if result.returncode:
        raise ValueError("macOS Vision could not inspect this capture")
    output = json.loads(result.stdout)
    boxes = output["boxes"]
    details = {}
    verified_boundaries = True
    if anchors is not None:
        first, second = output["boundary_boxes"]
        mode = "at_intersection" if intersection is not None else "between_labels"
        details = {mode: list(anchors), "boundary_boxes": [first, second], "unscoped_match_count": len(boxes)}
        verified_boundaries = len(first) == len(second) == 1
        if not verified_boundaries:
            boxes = []
        elif intersection is not None:
            boxes = [box for box in boxes if aligned_cell(box, first[0], second[0])]
        else:
            verified_boundaries = first[0][3] < second[0][1]
            boxes = [box for box in boxes if first[0][3] <= box[1] and box[3] <= second[0][1]]
    state = "not found" if not boxes else "unique label" if len(boxes) == 1 else "ambiguous label"
    return {"state": state if verified_boundaries else "unverified boundaries", "label_boxes": boxes,
            "viewport": list(source.size), "recognizer_revision": output["recognizer_revision"], **details}


def locate_exact_text(path, target, boundaries=None, *, intersection=None):
    os.umask(0o077)
    source, digest = load_png(path)
    result = exact_text_bounds(source, target, boundaries, intersection=intersection)
    root = Path(os.environ["HERMES_HOME"]) / "visual-reviews"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="label-", dir=root))
    record = dict(method="macos-exact-text", target=target, source=str(path.resolve()), source_sha256=digest, **result)
    receipt = folder / "location.json"
    receipt.write_text(json.dumps(record, indent=2))
    print(json.dumps({"receipt": str(receipt), **record}, ensure_ascii=False))
    return result["state"]


def locate_capture(path, target):
    if not target.strip():
        raise ValueError("describe the element to locate")
    os.umask(0o077)
    source, digest = load_png(path)
    if source.width < 6 or source.height < 8:
        raise ValueError("capture is too small for the location grid")
    root = Path(os.environ["HERMES_HOME"]) / "visual-reviews"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="locate-", dir=root))
    grid = folder / "grid.png"
    grid_image(source).save(grid)
    launcher = Path(__file__).resolve().parents[4] / "theseus"
    def ask(image, instruction):
        return ask_image(launcher, image, f"Call vision_analyze on {image}. Inspect only its pixels. "
                         f"Requested element: {target}. {instruction}")
    first = ask(grid, "Columns A–F and rows 1–8 are labeled in the margins. "
                "Return only the cell containing the center of the requested element, e.g. B3, "
                "or ABSENT if it is not identifiable.")
    cell = first["answer"].strip().upper()
    result = dict(source=str(path.resolve()), source_sha256=digest, viewport=list(source.size),
                  target=target, first=first, state="unverified")
    if cell == "ABSENT":
        result["state"] = "not found"
    elif re.fullmatch("[A-F][1-8]", cell):
        box = cell_box(source.size, cell, padding=True)
        crop = folder / "crop.png"
        source.crop(box).save(crop)
        second = ask(crop, "Does this crop visibly contain that exact element? Return YES or NO on the first "
                     "line, and describe its visible label and context on the next line. If uncertain return NO.")
        result.update(cell=cell, cell_box=cell_box(source.size, cell), inspected_box=box, inspection=second)
        if second["answer"].strip().splitlines()[:1] == ["YES"]:
            result["state"] = "verified in crop"
    receipt = folder / "location.json"
    receipt.write_text(json.dumps(result, indent=2))
    print(json.dumps({"receipt": str(receipt), **result}, ensure_ascii=False))


def main():
    if sys.argv[1:2] == ["locate"]:
        locate = argparse.ArgumentParser(prog="theseus vision locate")
        locate.add_argument("capture", type=Path)
        locate.add_argument("target")
        locate.add_argument("--exact-text", action="store_true", help="find all exact OCR label matches on macOS")
        scope = locate.add_mutually_exclusive_group()
        scope.add_argument("--at-intersection", nargs=2, metavar=("ROW", "COLUMN"),
                           help="find a target aligned with a unique heading to its left and one above")
        scope.add_argument("--between-labels", nargs=2, metavar=("UPPER", "LOWER"),
                            help="retain complete target labels in the band between two unique visible labels")
        locate.add_argument("--require-unique", action="store_true", help="exit 2 unless exactly one label is verified")
        args = locate.parse_args(sys.argv[2:])
        if (args.between_labels or args.at_intersection or args.require_unique) and not args.exact_text:
            locate.error("label boundaries and unique-label checks require --exact-text")
        if args.exact_text:
            state = locate_exact_text(args.capture, args.target, args.between_labels, intersection=args.at_intersection)
            return 2 if args.require_unique and state != "unique label" else 0
        else:
            locate_capture(args.capture, args.target)
        return
    parser = argparse.ArgumentParser(prog="theseus vision", epilog="To locate one element, use theseus vision locate --help.")
    parser.add_argument("before", type=Path, nargs="?")
    parser.add_argument("after", type=Path, nargs="?")
    parser.add_argument("--check", type=Path, metavar="RECEIPT")
    parser.add_argument("--region", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--expect", choices=("changed", "unchanged"), help="fail the comparison if the selected pixels violate this expectation")
    parser.add_argument("--question", help="request a visual interpretation from the current Codex subscription")
    args = parser.parse_args()
    if args.check:
        if args.before or args.after or args.question or args.region or args.expect:
            parser.error("--check takes only a vision receipt")
        try:
            status = check_receipt(args.check)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            status = "unverifiable: " + str(exc)
        print(json.dumps({"evidence": status}))
        return 0 if status == "current" else 2
    if not args.before or not args.after:
        parser.error("supply both captures or --check RECEIPT")
    os.umask(0o077)
    before, first_hash = load_png(args.before)
    after, second_hash = load_png(args.after)
    metrics, a, b = compare(before, after, args.region)
    root = Path(os.environ["HERMES_HOME"]) / "visual-reviews"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="view-", dir=root))
    record = {**metrics, "before": str(args.before.resolve()), "before_sha256": first_hash,
              "after": str(args.after.resolve()), "after_sha256": second_hash}
    if args.expect:
        record["assertion"] = pixel_assertion(args.expect, metrics["changed_pixels"])
    pair = comparison_sheet(a, b)
    png = run / "comparison.png"
    pair.save(png)
    with png.open("rb") as file:
        record["comparison_sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
    receipt = run / "comparison.json"
    receipt.write_text(json.dumps(record, indent=2))
    if args.question and metrics["changed_pixels"]:
        launcher = Path(__file__).resolve().parents[4] / "theseus"
        record["question"] = args.question
        record["review"] = review_pair(launcher, png, args.question)
        receipt.write_text(json.dumps(record, indent=2))
    print(json.dumps({"receipt": str(receipt), **record}, ensure_ascii=False))
    return 2 if args.expect and not record["assertion"]["passed"] else 0


if __name__ == "__main__":
    sys.exit(main())
