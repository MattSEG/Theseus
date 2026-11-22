"""Deterministic, single-batch planning with explicit cash timing."""

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from .briefs import checked_cell
from .library import field

COLUMNS = (
    "price",
    "cost",
    "quantity",
    "fixed",
    "lead_days",
    "supplier_days",
    "customer_days",
)
DDL = """CREATE TABLE IF NOT EXISTS scenarios(id INTEGER PRIMARY KEY,project TEXT NOT NULL,
 title TEXT NOT NULL, inputs TEXT NOT NULL, result TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""


def numeric(args, key, integer=False):
    try:
        v = Decimal(str(args.get(key)))
    except InvalidOperation:
        raise ValueError(f"Supply a number for {key}.") from None
    if (
        not v.is_finite()
        or v < 0
        or v > 10**9
        or (integer and v != v.to_integral_value())
        or v.as_tuple().exponent < -8
    ):
        raise ValueError(
            f"Use a nonnegative {'whole ' if integer else ''}number for {key}, up to one billion and eight decimals."
        )
    return v


def money(v):
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate(args):
    values = {
        k: numeric(
            args, k, k in ("quantity", "lead_days", "supplier_days", "customer_days")
        )
        for k in COLUMNS
    }
    qty = values["quantity"]
    if not qty:
        raise ValueError("Use a positive batch quantity.")
    price, cost, fixed = (values[k] for k in ("price", "cost", "fixed"))
    unit = price - cost
    receipt = values["lead_days"] + values["customer_days"]
    return dict(
        contribution=money(unit),
        profit=money(unit * qty - fixed),
        break_even_units=int((fixed / unit).to_integral_value(rounding=ROUND_CEILING))
        if unit > 0
        else (0 if fixed == 0 else None),
        price_floor=str(
            (cost + fixed / qty).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        ),
        cash_before_collection=money(
            fixed + (cost * qty if values["supplier_days"] <= receipt else 0)
        ),
        first_receipt_day=int(receipt),
    )


def operate(db, project, args):
    db.execute(DDL)
    if args["action"] == "scenario_save":
        title = field(args, "title", 180)
        currency = field(args, "currency", 3).upper()
        if not re.fullmatch("[A-Z]{3}", currency):
            raise ValueError("Use a three-letter currency code.")
        result = calculate(args)
        inputs = {k: str(args[k]) for k in COLUMNS}
        inputs["currency"] = currency
        if args.get("edition") or args.get("quote"):
            inputs["cost_reference"] = checked_cell(
                db,
                project,
                dict(
                    value=inputs["cost"],
                    edition=args.get("edition"),
                    quote=args.get("quote"),
                ),
            )
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """INSERT INTO scenarios(project,title,inputs,result) SELECT ?1,?2,?3,?4
               WHERE NOT EXISTS(SELECT 1 FROM scenarios WHERE project=?1 AND title=?2 AND inputs=?3)""",
            (project, title, json.dumps(inputs), json.dumps(result)),
        )
    elif args["action"] != "scenario_list":
        raise ValueError("Unknown scenario operation.")
    saved = []
    for r in db.execute(
        "SELECT id,title,inputs,result,created FROM scenarios WHERE project=? ORDER BY id DESC LIMIT 100",
        (project,),
    ):
        result = dict(r)
        result.update(inputs=json.loads(r["inputs"]), result=json.loads(r["result"]))
        saved.append(result)
    return {"scenarios": saved}
