import importlib.util
from pathlib import Path

import pytest


source = Path(__file__).resolve().parents[2] / "skills/software-development/learning-run/scripts/junit_evidence.py"
spec = importlib.util.spec_from_file_location("junit_evidence", source)
junit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(junit)


def test_counts_observed_nested_cases_and_withholds_test_output():
    xml = """<testsuites tests="4" failures="1" errors="1" skipped="1"><testsuite tests="2">
        <testcase name="a"><system-out>private fixture output</system-out></testcase>
        <testcase name="b"><failure>failed assertion details</failure></testcase></testsuite>
        <testsuite><testcase name="c"><error>stack trace</error></testcase>
        <testcase name="d"><skipped/></testcase></testsuite></testsuites>"""
    text = junit.observations(xml)
    assert "4 testcase outcomes. Passed: 1. Failed: 1. Errors: 1. Skipped: 1." in text
    assert "private fixture output" not in text and "stack trace" not in text
    assert "failed: b" in text


@pytest.mark.parametrize("xml", [
    '<testsuite tests="2"><testcase/></testsuite>',
    '<testsuite failures="0"><testcase><failure/></testcase></testsuite>',
    '<testsuite><testcase status="notrun"/></testsuite>',
    '<testsuite><testcase status="passed"><error/></testcase></testsuite>',
    '<testsuite><testcase><skipped/><failure/></testcase></testsuite>',
    '<!DOCTYPE testsuite><testsuite/>',
    '<unrelated/>',
])
def test_refuses_inconsistent_or_unrecognized_xml(xml):
    with pytest.raises(ValueError):
        junit.observations(xml)


def test_reports_empty_suite_and_namespaced_junit():
    assert "0 testcase outcomes" in junit.observations('<testsuite tests="0"/>')
    xml = '<j:testsuite xmlns:j="urn:test"><j:testcase name="good"/></j:testsuite>'
    assert "Passed: 1." in junit.observations(xml)


def test_scoped_cases_keep_whole_report_counts_and_verify_siblings():
    xml = """<testsuites tests="3"><testsuite name="src/ui.test.ts"><testcase name="one"/>
       <testsuite name="other/nested"><testcase name="two"><skipped/></testcase></testsuite></testsuite>
       <testsuite name="scripts/tool.test.ts" failures="1"><testcase name="three"><failure/></testcase></testsuite></testsuites>"""
    scope = junit.observations(xml, "src/")
    assert "3 testcase outcomes. Passed: 1. Failed: 1. Errors: 0. Skipped: 1." in scope
    assert "1 distinct suite names contribute 1 testcase outcomes" in scope
    assert "Scoped counts: 1 passed, 0 failed, 0 errors, 0 skipped." in scope
    with pytest.raises(ValueError, match="disagrees"):
        junit.observations(xml.replace('failures="1"', 'failures="0"'), "src/")
    for prefix in ("missing/", "src", "../", "/"):
        with pytest.raises(ValueError):
            junit.observations(xml, prefix)
