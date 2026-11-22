"""Reproducible, standard-library-only descriptive statistics."""

import csv
import io
from decimal import Decimal, InvalidOperation, localcontext


def records(body):
    first = body.splitlines()[0] if body.strip() else ""
    delimiter = "\t" if "\t" in first and "," not in first else ","
    try:
        reader = csv.reader(
            io.StringIO(body.lstrip("\ufeff")), delimiter=delimiter, strict=True
        )
        header = next(reader, [])
        if (
            not header
            or len(header) > 150
            or len(set(header)) != len(header)
            or any(not s.strip() for s in header)
        ):
            raise ValueError("Use a CSV or TSV with distinct nonempty column headers.")
        result = list(reader)
    except csv.Error as exc:
        raise ValueError("Malformed CSV or TSV.") from exc
    if len(result) > 15000:
        raise ValueError("Select up to 15,000 records per run.")
    return header, result


def summarize(source, params, *, parsed_table=None):
    header, data = parsed_table if parsed_table is not None else records(source["body"])
    measure = params["measure"]
    group = params["group"]
    if measure not in header or (group and group not in header) or measure == group:
        raise ValueError("Choose distinct existing measurement and grouping columns.")
    groups = {}
    excluded = []
    for number, row in enumerate(data, 2):
        if len(row) != len(header):
            excluded.append({"record": number, "reason": "wrong field count"})
            continue
        label = row[header.index(group)].strip() if group else "All records"
        try:
            value = Decimal(row[header.index(measure)].strip())
        except InvalidOperation:
            value = Decimal("NaN")
        if (
            not label
            or not value.is_finite()
            or abs(value) > 10**18
            or value.as_tuple().exponent < -18
        ):
            excluded.append(
                {"record": number, "reason": "missing group or invalid measurement"}
            )
            continue
        groups.setdefault(label, []).append((number, value))
    if not groups:
        raise ValueError("No usable measurement records.")
    if len(groups) > 150:
        raise ValueError("Choose a grouping column with 150 or fewer groups.")
    output = []
    with localcontext() as ctx:
        ctx.prec = 48
        for label, values in sorted(groups.items()):
            n = len(values)
            mean = sum(v for _, v in values) / n
            minimum = min(values, key=lambda p: p[1])
            maximum = max(values, key=lambda p: p[1])
            output.append(
                dict(
                    group=label,
                    n=n,
                    mean=str(mean.quantize(Decimal(".000001")).normalize()),
                    minimum=str(minimum[1]),
                    maximum=str(maximum[1]),
                    min_record=minimum[0],
                    max_record=maximum[0],
                )
            )
    return {
        "groups": output,
        "excluded_count": len(excluded),
        "excluded": excluded[:100],
        "records": len(data),
        "digest": source["digest"],
    }
