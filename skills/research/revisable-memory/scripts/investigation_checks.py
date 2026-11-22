"""Prospective, scoped predictions and updates for the investigation ledger."""

import json
from itertools import combinations


def validate_measurement(check):
    measurement = check["measurement"]
    if not isinstance(measurement, dict) or set(measurement) != {
        "field",
        "scope",
        "values",
    }:
        raise ValueError("a measurable check needs a field, scope and outcome values")
    field, scope, values = (measurement[k] for k in ("field", "scope", "values"))
    if (
        not isinstance(field, str)
        or not field.strip()
        or len(field) > 100
        or ": " in field
    ):
        raise ValueError("invalid measured field")
    if (
        not isinstance(scope, dict)
        or not 1 <= len(scope) <= 8
        or field in scope
        or not all(
            isinstance(k, str)
            and isinstance(v, str)
            and k.strip()
            and v.strip()
            and "\n" not in k + v
            and len(k + v) <= 300
            for k, v in scope.items()
        )
    ):
        raise ValueError("declare the subject and conditions as exact source fields")
    if (
        not isinstance(values, dict)
        or set(values) != set(check["outcomes"])
        or not all(
            isinstance(v, str) and v.strip() and "\n" not in v and len(v) <= 300
            for v in values.values()
        )
        or len(set(values.values())) != len(values)
    ):
        raise ValueError("each distinct outcome needs its own literal source value")


def classify(proof, measurement, quote, parse):
    if proof["kind"] != "specimen" or proof["coverage"] != "whole section":
        return None, "Original evidence is background or the scope is incomplete."
    value, reason = parse(proof["context"], measurement["scope"], measurement["field"])
    if value is None:
        return None, reason
    if quote != measurement["field"] + ": " + value:
        return (
            None,
            "The submitted observation is not the expected measurement in that record.",
        )
    for outcome, expected in measurement["values"].items():
        if value == expected:
            return outcome, "The scoped measurement matched this predeclared outcome."
    return None, "The recorded result fell outside the predeclared outcomes."


def active_result(row):
    return (
        row["outcome"] is not None
        and row["prospective"]
        and "superseded_by" not in row
        and row.get("source_status") not in ("superseded", "conflict")
        and row.get("status_when_attached") not in ("superseded", "conflict")
    )


def assess(alternatives, results):
    """Intersect predictions within identical scopes; keep unmatched and untested cases open."""
    live = {
        item["name"] for item in alternatives if item["status"] in ("open", "supported")
    }
    groups = {}
    for row in results:
        if not active_result(row):
            continue
        scope_key = json.dumps(row["scope"], sort_keys=True)
        group = groups.setdefault(
            scope_key,
            {
                "scope": row["scope"],
                "compatible": set(live),
                "tested": set(),
                "results": [],
                "by_check": {},
            },
        )
        group["by_check"].setdefault((row["plan"], row["key"]), set()).add(
            row["outcome"]
        )
        predicts = row["predictions"]
        group["compatible"] -= {
            t for t in live if t in predicts and predicts[t] != row["outcome"]
        }
        group["tested"].update(t for t in live if t in predicts)
        group["results"].append(
            {
                "revision": row["revision"],
                "plan": row["plan"],
                "outcome": row["outcome"],
                "citation": row["citation"],
            }
        )
    assessed = []
    for group in groups.values():
        compatible = sorted(group["compatible"])
        tested = group["tested"]
        group["tested"] = sorted(tested)
        group["compatible"] = compatible
        group["untested"] = sorted(live - tested)
        conflict = any(len(values) > 1 for values in group.pop("by_check").values())
        group["status"] = "unresolved"
        if conflict:
            group["status"] = "conflicting results"
            group["next_check"] = (
                "Reconcile the source identities, tested scope and original observations for the same predeclared check."
            )
        elif not compatible:
            group["status"] = (
                "none of the registered alternatives fits all measured checks"
            )
            group["next_check"] = (
                "Inspect the controls and consider an additional explanation with a distinct prediction."
            )
        if not conflict and len(compatible) == 1 and len(live) > 1 and tested == live:
            group["status"] = "provisional among tested alternatives"
            group["leader"] = compatible[0]
        assessed.append(group)
    return assessed


def ranked_checks(candidates, alternatives, results):
    active = {t["name"] for t in alternatives if t["status"] in ("supported", "open")}
    completed = {(r["plan"], r["key"]) for r in results if active_result(r)}
    ranked = []
    for candidate in candidates.values():
        if (candidate["revision"], candidate["key"]) in completed:
            continue
        pred = candidate["predictions"]
        pairs = [
            list(pair)
            for pair in combinations(sorted(active.intersection(pred)), 2)
            if pred[pair[0]] != pred[pair[1]]
        ]
        if pairs:
            ranked.append({**candidate, "separates": pairs})
    return sorted(ranked, key=lambda c: (-len(c["separates"]), c["minutes"], c["key"]))
