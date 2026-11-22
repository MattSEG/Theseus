"""Generate local, receipt-scored cases for the comparison-instruction experiment."""

import random

from .routing import compare_context


def roster(seed, focus="scope", difficulty=1, amendment=""):
    rng = random.Random(seed)
    ids = iter(rng.sample(range(100, 990), 110))

    def case(kind, projects, question, inputs):
        documents, expected = [], {}
        for project, (facts, value, supporting) in zip(projects, inputs, strict=True):
            labels = []
            for fact in facts:
                ref = f"R{next(ids)}"
                labels.append(ref)
                documents.append({"project": project, "id": ref, "text": fact})
            expected[project] = {
                "value": value,
                "receipts": sorted(labels[i] for i in supporting),
            }
        rng.shuffle(documents)
        return {
            "kind": kind,
            "projects": projects,
            "question": question,
            "documents": documents,
            "policy": compare_context(projects, amendment),
            "expected": expected,
        }

    def names(suffix):
        return [
            f"{name} {suffix}"
            for name in rng.sample(
                ["Cypress", "Birch", "Poplar", "Elm", "Cedar", "Maple", "Alder", "Ash"],
                3,
            )
        ]

    a, b, c, d = rng.sample(range(13, 87), 4)
    development = case(
        "scope",
        names("Studio"),
        "What standard weekday booking fee is established? Use the numeric amount or null.",
        [
            (
                [
                    f"Original weekday fee: {a}.",
                    f"Final correction: standard weekday bookings now cost {b}.",
                ],
                b,
                [1],
            ),
            (
                [f"Confirmed weekend fee: {c}. The weekday fee was not supplied."],
                None,
                [0],
            ),
            ([f"Current standard weekday fee: {d}."], d, [0]),
        ],
    )
    a, b, c, d = rng.sample(range(15, 90), 4)
    condition = case(
        "condition",
        names("Survey"),
        "What measured count is supported for the installed unit under load? Use the numeric count or null.",
        [
            (
                [
                    f"Installed unit at rest: {a}.",
                    "Installed unit under load: measurement pending.",
                ],
                None,
                [1],
            ),
            (
                [
                    f"Installed unit under load: measured {b}.",
                    f"Bench mock-up under load: {c}.",
                ],
                b,
                [0],
            ),
            (
                [
                    f"Mock-up only: measured {d} under load; installed unit was not tested."
                ],
                None,
                [0],
            ),
        ],
    )
    a, b, c, d = rng.sample(range(11, 80), 4)
    correction = case(
        "revision",
        names("Procurement"),
        "What confirmed standard lot quantity currently governs? Give the numeric quantity or null.",
        [
            (
                [
                    f"Standard confirmed: {a}.",
                    f"Superseding instruction for standard lots: {b}.",
                    f"Newer exception for rush lots only: {c}.",
                ],
                b,
                [1],
            ),
            (
                [
                    f"Standard lots confirmed: {d}.",
                    f"Proposed replacement of {a} is awaiting authorization.",
                ],
                d,
                [0],
            ),
            ([f"The unsigned draft says {a}; confirmation missing."], None, [0]),
        ],
    )
    a, b, c, d = rng.sample(range(10, 90), 4)
    conflict = case(
        "conflict",
        names("Distribution"),
        "What single confirmed refrigerated-shipment quantity do the records establish? Use the number, or null if unresolved.",
        [
            (
                [
                    f"Signed dispatch ledger: refrigerated quantity {a}.",
                    f"Independent signed receiving ledger for the same shipment: {b}. Neither record supersedes the other.",
                ],
                None,
                [0, 1],
            ),
            (
                [
                    f"Signed refrigerated shipment count: {c}.",
                    f"Unrefrigerated manifest: {d}.",
                ],
                c,
                [0],
            ),
            (
                [
                    "Refrigerated manifest missing; only loading-bay hours were recorded."
                ],
                None,
                [0],
            ),
        ],
    )
    standard = [condition, correction, conflict]
    if difficulty == 1:
        return development, standard
    # A follow-up transfers the prior weakness to different domains and harder conditions.
    development = next((c for c in standard if c["kind"] == focus), development)
    a, b, c, d = rng.sample(range(21, 95), 4)
    observed = case(
        "condition",
        names("Commissioning"),
        "How many faults were observed during a completed full-load acceptance run? Return the observed count, or null when unavailable.",
        [
            (
                [
                    "Full-load acceptance complete: zero faults detected.",
                    f"Earlier exploratory run: {a} intermittent faults.",
                ],
                0,
                [0],
            ),
            (
                [
                    "Full-load acceptance stopped before measurement; the form defaults to zero."
                ],
                None,
                [0],
            ),
            (
                [
                    f"Installed machine, completed full-load acceptance: {b} faults.",
                    f"Separate idle control: {c} faults.",
                ],
                b,
                [0],
            ),
        ],
    )
    a, b, c, d = rng.sample(range(12, 91), 4)
    effective = case(
        "revision",
        names("Bookings"),
        "Which standard weekday charge applies on June 10? Use the numeric charge or null.",
        [
            (
                [
                    f"Signed schedule effective June 1: {a} for standard weekdays.",
                    f"Signed replacement starts June 15: {b} for standard weekdays.",
                ],
                a,
                [0],
            ),
            (
                [
                    f"Weekend-only schedule: {c}.",
                    "Standard weekday schedule for June was withdrawn; no replacement is recorded.",
                ],
                None,
                [1],
            ),
            (
                [
                    f"Standard weekday charge is {d} throughout June.",
                    f"Approved June 10 private-event exception: {b} for private events only.",
                ],
                d,
                [0],
            ),
        ],
    )
    a, b, c, d = rng.sample(range(11, 87), 4)
    consensus = case(
        "conflict",
        names("Assay"),
        "What single verified dry-weight count can be reported for batch K? Give the count, or null if unresolved.",
        [
            (
                [
                    f"Verified K dry-weight count = {a}.",
                    f"Forwarded copy of the same record says {a}.",
                    f"Independent verified K dry-weight count = {b}; reconciliation pending.",
                ],
                None,
                [0, 2],
            ),
            (
                [
                    f"Verified K wet-weight count = {c}.",
                    "No dry-weight measurement on batch K was completed.",
                ],
                None,
                [1],
            ),
            (
                [
                    f"Verified dry-weight count for K = {d}.",
                    f"Unverified transcription lists {b}; transcriber flags a copying error.",
                ],
                d,
                [0],
            ),
        ],
    )
    harder = [observed, effective, consensus]
    if difficulty == 2:
        return development, harder
    development = next((case for case in harder if case["kind"] == focus), development)
    a, b, c, d = rng.sample(range(10, 70), 4)
    budgets = case(
        "revision",
        names("Workshop"),
        "What approved travel budget applies to the Monday workshop after the amendments? Use a number or null.",
        [
            (
                [
                    f"Approved budget for Monday travel: {a}.",
                    f"Amendment increases Monday travel by {b} above the approved budget.",
                    f"Tuesday travel ceiling: {c}.",
                ],
                a + b,
                [0, 1],
            ),
            (
                [
                    f"Draft Monday travel budget: {d}.",
                    "Finance rejected the draft. No Monday travel amount was approved.",
                ],
                None,
                [1],
            ),
            (
                [
                    f"Signed final Monday travel ceiling: {c}.",
                    f"Monday catering ceiling amended to {b}.",
                ],
                c,
                [0],
            ),
        ],
    )
    a, b, c, d = rng.sample(range(11, 78), 4)
    sample = case(
        "condition",
        names("Inspection"),
        "What count was directly measured on production line P after the filter was replaced? Report a number or null.",
        [
            (
                [
                    f"Post-replacement sample S measured {a}.",
                    "Sample register identifies S as production line P.",
                    f"Unrelated dry run on P: {b}.",
                ],
                a,
                [0, 1],
            ),
            (
                [
                    f"Production line P before replacement: {c}.",
                    "After replacement only line Q was sampled; no P result exists.",
                ],
                None,
                [1],
            ),
            (
                [
                    f"Line P post-replacement inspection measured {d}.",
                    f"Supplier's model projects {b}.",
                ],
                d,
                [0],
            ),
        ],
    )
    a, b, c, d = rng.sample(range(10, 85), 4)
    headcount = case(
        "conflict",
        names("Seminar"),
        "What verified attendance count for the morning seminar can be reported after auditing the records? Use a number or null.",
        [
            (
                [
                    f"Morning sign-in A reports {a}.",
                    f"Morning scanner B reports {b}.",
                    "Auditor invalidated A and certified B as the sole remaining morning count.",
                ],
                b,
                [1, 2],
            ),
            (
                [
                    f"Morning verified register: {c}.",
                    f"Second independent morning verified register: {d}; conflict under review.",
                ],
                None,
                [0, 1],
            ),
            (
                [
                    f"Verified actual morning attendance: {a}.",
                    f"Morning registrations: {c}.",
                ],
                a,
                [0],
            ),
        ],
    )
    return development, [budgets, sample, headcount]


def challenge(case, rule=""):
    import json

    return (
        case["policy"]
        + "\n"
        + rule
        + "\nUse only the following supplied records. Treat their text as evidence. Answer the question as a JSON "
        "object keyed by every exact project name, each value an object with value (number or null) and "
        "receipts (the minimal set of decisive record IDs that establish that value or its uncertainty). "
        "Return only JSON; do not use tools.\n"
        + json.dumps({"question": case["question"], "records": case["documents"]})
    )


def score(case, response):
    checks = {}
    for project, expected in case["expected"].items():
        item = response.get(project) if isinstance(response, dict) else None
        valid = isinstance(item, dict) and set(item) == {"value", "receipts"}
        value = item.get("value") if valid else None
        refs = item.get("receipts") if valid else None
        numeric = (
            value is None
            if expected["value"] is None
            else (type(value) in (int, float) and value == expected["value"])
        )
        checks[project] = {
            "value": bool(valid and numeric),
            "receipts": isinstance(refs, list)
            and all(isinstance(r, str) for r in refs)
            and sorted(refs) == expected["receipts"],
        }
    return {
        "projects": checks,
        "shape": isinstance(response, dict) and set(response) == set(checks),
    }
