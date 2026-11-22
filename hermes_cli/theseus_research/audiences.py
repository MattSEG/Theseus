"""Private planning and explicitly selected public facts from the same revision."""

import hashlib
import html
import json
from . import briefs
from .bundles import publish
from .library import field

DDL = """CREATE TABLE IF NOT EXISTS public_brief_cells(brief INTEGER NOT NULL,revision INTEGER NOT NULL,
 row_name TEXT NOT NULL,column_name TEXT NOT NULL,public_item TEXT NOT NULL,public_label TEXT NOT NULL,classification TEXT NOT NULL,
 PRIMARY KEY(brief,revision,row_name,column_name),UNIQUE(brief,revision,public_item,public_label))"""
CLASSES = ("Measured", "Quoted", "Estimate", "Assumption", "Decision")


def operate(db, project, args):
    db.executescript(briefs.DDL)
    db.execute(DDL)
    action = args["action"]
    if action not in (
        "audience_list",
        "audience_approve",
        "audience_remove",
        "audience_export",
    ):
        raise ValueError("Unknown edition operation.")
    if action != "audience_list":
        db.execute("BEGIN IMMEDIATE")
    doc = briefs.get(db, project, args.get("brief"))
    bid = doc["brief"]
    rev = doc["revision"]
    if rev != args.get("revision"):
        raise ValueError(
            "The brief changed. Reopen this revision and approve its public fields."
        )
    rows = {r["name"]: r["cells"] for r in doc["rows"]}
    if action in ("audience_approve", "audience_remove"):
        row = field(args, "row", 160)
        col = field(args, "column", 100)
        if action == "audience_approve":
            if not rows.get(row, {}).get(col, {}).get("value"):
                raise ValueError("Select a nonempty saved cell.")
            kind = args.get("classification")
            if kind not in CLASSES:
                raise ValueError("Label the fact or estimate.")
            db.execute(
                """INSERT INTO public_brief_cells VALUES(?,?,?,?,?,?,?) ON CONFLICT(brief,revision,row_name,column_name)
                DO UPDATE SET public_item=excluded.public_item,public_label=excluded.public_label,classification=excluded.classification""",
                (
                    bid,
                    rev,
                    row,
                    col,
                    field(args, "public_item", 160),
                    field(args, "public_label", 160),
                    kind,
                ),
            )
        else:
            db.execute(
                "DELETE FROM public_brief_cells WHERE brief=? AND revision=? AND row_name=? AND column_name=?",
                (bid, rev, row, col),
            )
    approved = []
    for r in db.execute(
        "SELECT * FROM public_brief_cells WHERE brief=? AND revision=? ORDER BY public_item,public_label",
        (bid, rev),
    ):
        approved.append(
            dict(
                row=r["row_name"],
                column=r["column_name"],
                item=r["public_item"],
                label=r["public_label"],
                classification=r["classification"],
                value=rows[r["row_name"]][r["column_name"]]["value"],
            )
        )
    if action != "audience_export":
        return {"approved": approved}
    if not approved:
        raise ValueError("Approve at least one public cell in this revision.")
    title = field(args, "public_title", 180)
    esc = html.escape
    content = "".join(
        "<tr>"
        + "".join(
            "<td>" + esc(a[k]) + "</td>"
            for k in ("item", "label", "value", "classification")
        )
        + "</tr>"
        for a in approved
    )
    h = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>{esc(title)}</title>
<style>body{{max-width:56rem;margin:4rem auto;padding:0 1rem;color:#172c32;font:16px/1.5 system-ui}}h1{{font:2.3rem Georgia,serif}}table{{border-collapse:collapse;width:100%}}th,td{{padding:.55rem;text-align:left;border-bottom:1px solid #bbc9c8}}</style>
<h1>{esc(title)}</h1><table><thead><tr><th>Item</th><th>Measure</th><th>Value</th><th>Basis</th></tr></thead><tbody>{content}</tbody></table></html>"""
    internal = briefs.csv_text(doc).replace("Item,", "Internal item,", 1).encode()
    files = {"internal/planning.csv": internal, "external/brief.html": h.encode()}
    hashes = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
    fingerprint = hashlib.sha256(
        json.dumps(hashes, sort_keys=True).encode()
    ).hexdigest()[:16]
    receipt = dict(brief=bid, revision=rev, hashes=hashes, public_fields=len(approved))
    files["receipt.json"] = json.dumps(receipt, indent=2).encode()
    base = publish(f"audience-{bid}-r{rev}-{fingerprint}", files)
    return dict(
        approved=approved,
        internal=str(base / "internal/planning.csv"),
        external=str(base / "external/brief.html"),
        receipt=str(base / "receipt.json"),
    )
