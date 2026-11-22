"""Describe saved JUnit testcase outcomes without importing reporter output or stack traces."""
from collections import Counter
import re
from xml.etree import ElementTree


def observations(raw, suite_prefix=None):
    if suite_prefix is not None and (not isinstance(suite_prefix, str) or len(suite_prefix) > 240
            or not re.fullmatch(r"(?:[A-Za-z0-9_.-]+/)+", suite_prefix)
            or any(part in {".", ".."} for part in suite_prefix.split("/"))):
        raise ValueError("use a relative JUnit suite directory ending in /")
    scoped_counts = Counter()
    scoped_names = set()
    if "<!DOCTYPE" in raw or "<!ENTITY" in raw:
        raise ValueError("select a JUnit report without document type or entity declarations")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ValueError("invalid JUnit XML") from exc
    metadata = {"properties", "system-out", "system-err"}
    outcomes = {"failure": "failed", "error": "errors", "skipped": "skipped"}
    passed_examples, other_examples = [], []

    def tag(element):
        return element.tag.rsplit("}", 1)[-1]

    def walk(suite, depth):
        if depth > 24 or tag(suite) not in {"testsuite", "testsuites"}:
            raise ValueError("select a JUnit testsuite or testsuites report")
        counts = Counter()
        suite_name = suite.get("name", "")
        selected = suite_prefix is not None and tag(suite) == "testsuite" and suite_name.startswith(suite_prefix)
        for item in suite:
            name = tag(item)
            if name in {"testsuite", "testsuites"}:
                counts.update(walk(item, depth + 1))
            elif name == "testcase":
                markers = [tag(child) for child in item if tag(child) not in metadata]
                if len(markers) > 1 or any(marker not in outcomes for marker in markers):
                    raise ValueError("unsupported or ambiguous JUnit testcase outcome")
                status = item.get("status", "").casefold()
                if status not in {"", "run", "passed", "completed"}:
                    raise ValueError("unsupported JUnit testcase status")
                if status == "passed" and markers:
                    raise ValueError("contradictory JUnit testcase status")
                outcome = outcomes[markers[0]] if markers else "passed"
                counts[outcome] += 1
                if selected:
                    scoped_counts[outcome] += 1
                    scoped_names.add(suite_name)
                examples, limit = (passed_examples, 4) if outcome == "passed" else (other_examples, 12)
                if len(examples) < limit:
                    label = " ".join(item.get("name", "(unnamed)").split())[:200]
                    examples.append(f"{outcome}: {label}")
            elif name not in metadata:
                raise ValueError("unsupported element in JUnit suite")
        for attribute, value in (("tests", sum(counts.values())), ("failures", counts["failed"]),
                                 ("errors", counts["errors"]), ("skipped", counts["skipped"])):
            saved = suite.get(attribute)
            if saved is not None and (not saved.isascii() or not saved.isdigit() or len(saved) > 12 or int(saved) != value):
                raise ValueError(f"declared JUnit {attribute} disagrees with testcase outcomes")
        return counts

    counts = walk(root, 0)
    total = sum(counts.values())
    summary = (f"JUnit records {total} testcase outcomes. Passed: {counts['passed']}. "
               f"Failed: {counts['failed']}. Errors: {counts['errors']}. Skipped: {counts['skipped']}.")
    if suite_prefix is not None:
        if not scoped_counts:
            raise ValueError("no testcase has a containing suite with that prefix")
        summary += (f" Within containing suite names beginning with {suite_prefix}, "
                    f"{len(scoped_names)} distinct suite names contribute {sum(scoped_counts.values())} testcase outcomes. "
                    f"Scoped counts: {scoped_counts['passed']} passed, {scoped_counts['failed']} failed, "
                    f"{scoped_counts['errors']} errors, {scoped_counts['skipped']} skipped.")
    if passed_examples or other_examples:
        summary += " Examples from the report: " + " | ".join(other_examples + passed_examples) + "."
    return summary
