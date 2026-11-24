"""Line items of an RFQ's price sheet (bid form), for pricing line by line.

A price sheet lists what is bought: item number, part number, description and
an estimated quantity, with blank price cells for the supplier. The rows are read
from the canonical PDF of every file, line by line, after a header row naming a
quantity and a description column; a spreadsheet's tables are read by column.
Only numbers printed on the sheet are taken: nothing is estimated, and a row
without a quantity is not a line item.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from autorfx.consistency.normalize import parse_number
from autorfx.models import PackageManifest, ParsedFile, Table
from autorfx.parser.pdf import ParseError, read_paragraphs

MAX_LINES = 200
QUANTITY = re.compile(r"\b(?:quantity|quantities|qty|menge|stückzahl|anzahl)\b|수량|물량", re.IGNORECASE)
DESCRIPTION = re.compile(
    r"\b(?:description|item|part|article|bezeichnung|artikel|benennung)\b|품명|품목|내역", re.IGNORECASE
)
YEARLY = re.compile(r"\b(?:yearly|annual|per year|jährlich|jahresbedarf)\b|연간", re.IGNORECASE)
GROUP = re.compile(r"^(?:category|lot|los|group|gruppe)\s*\S*\s*[–—:-]\s*(.+)$", re.IGNORECASE)
MONTHLY = re.compile(r"per month|monthly|pro monat|monatlich|월", re.IGNORECASE)
MONTHS = re.compile(r"^\s*(?:months|monate|개월)\s*$", re.IGNORECASE)
TOTAL = re.compile(r"\b(?:sub)?total\b|summe|합계", re.IGNORECASE)
END = re.compile(r"^(?:form|section|annex|exhibit|anlage)\b", re.IGNORECASE)
# "13 DB5Z-2078-F BRAKE LINE, RF-14 POL UTIL $ 2 $": item number, text, quantity, empty price cells.
ROW = re.compile(r"^(?P<no>\d{1,4})\s+(?P<body>.*?\S)\s+(?:[$€£]\s*)?(?P<qty>\d[\d,.]*)(?:\s*[$€£])*\s*$")
STARTS_ROW = re.compile(r"^\d{1,4}\s+\S")
WRAP = 3
PART = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9./-]{3,}$")


def _line(
    no: str, body: str, quantity: str, group: str | None, doc: str, page: int, yearly: bool
) -> dict | None:
    body = body.rstrip(" $€£").strip()
    amount = parse_number(quantity, "en")
    if amount is None or amount <= 0 or amount != int(amount) or len(body) < 3:
        return None
    first, _, rest = body.partition(" ")
    part = first if PART.match(first) and rest else None
    name = (rest if part else body).strip()
    if not any(c.isalpha() for c in name):
        return None
    return {
        "no": no,
        "part": part,
        "name": name[:200],
        "quantity": int(amount),
        "yearly": yearly,
        "group": group,
        "source_doc": doc,
        "page": page,
    }


def _from_pdf(doc: str, path: Path) -> list[dict]:
    try:
        paragraphs, _, _ = read_paragraphs(path)
    except ParseError:
        return []
    found: list[dict] = []
    active = False
    yearly = False
    group: str | None = None
    pending: list[str] = []
    run = 0
    for paragraph in paragraphs:
        # A table row may share a block with its neighbours: rows are read one rendered line at a time.
        by_line: dict[int, list[str]] = {}
        for word in paragraph.words:
            by_line.setdefault(word.line, []).append(word.text)
        texts = (
            [paragraph.text, *(" ".join(w) for w in by_line.values())]
            if len(by_line) > 1
            else [paragraph.text]
        )
        text = paragraph.text.strip()
        if QUANTITY.search(text) and DESCRIPTION.search(text) and len(text) <= 200:
            active, yearly = True, bool(YEARLY.search(text))
            continue
        heading = GROUP.match(text)
        if heading and not TOTAL.search(text):
            group = heading.group(1).strip()[:80]
            run += 1
            continue
        if not active:
            continue
        if END.match(text) and "price" not in text.lower() and "preis" not in text.lower():
            active = False
            continue
        match = ROW.match(texts[0].strip())
        if match is None and pending:
            # A row whose description wrapped: "10 8C2Z-17626-AACP" / "PANEL …" / "$ 2 $".
            pending.append(text)
            match = ROW.match(" ".join(pending))
            if match is None and len(pending) < WRAP:
                continue
        pending = []
        rows = [match] if match else [ROW.match(t.strip()) for t in texts[1:]]
        if not any(rows) and STARTS_ROW.match(text):
            pending = [text]
            continue
        for row in rows:
            if row is None:
                continue
            line = _line(row["no"], row["body"], row["qty"], group, doc, paragraph.page, yearly)
            if line:
                line["run"] = (doc, run)
                found.append(line)
    return found


def _find(header: Sequence[str], pattern: str | re.Pattern[str], skip: int | None = None) -> int | None:
    regex = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) else pattern
    return next((i for i, c in enumerate(header) if i != skip and regex.search(c)), None)


def _columns(header: Sequence[str]) -> tuple[int, int, int | None, int | None] | None:
    """Columns of quantity, description, part number and item number, when the row is a header."""
    quantity = _find(header, QUANTITY)
    description = _find(header, r"description|bezeichnung|benennung|품명|내역")
    if description is None:
        description = _find(header, DESCRIPTION, skip=quantity)
    if quantity is None or description is None:
        return None
    part = _find(header, r"part\s*(no|number|#)|teilenummer|sachnummer|품번")
    number = _find(header, r"^\s*(item\s*#|item\s*no|pos\.?|no\.?|#)\s*$")
    return quantity, description, part, number


def _from_tables(doc: str, tables: Iterable[Table]) -> list[dict]:
    found: list[dict] = []
    for table in tables:
        header: Sequence[str] = table.header
        columns = _columns(header)
        for row in table.rows:
            if columns is None:
                columns, header = _columns(row), row
                continue
            yearly = any(YEARLY.search(c) for c in header) or any(MONTHS.match(c) for c in header)
            quantity, description, part, number = columns

            def cell(i: int | None, row: Sequence[str] = row) -> str:
                return row[i].strip() if i is not None and i < len(row) else ""

            name = cell(description)
            if not name or TOTAL.search(name):
                continue
            amount = cell(quantity)
            months = next((i for i, c in enumerate(header) if MONTHS.match(c)), None)
            if months is not None and MONTHLY.search(header[quantity]):
                # "MAX QUANTITY Per month" × "Months": the quantity of the ordering period.
                per_month, count = parse_number(amount, "en"), parse_number(cell(months), "en")
                amount = str(per_month * count) if per_month is not None and count is not None else ""
            line = _line(
                cell(number) or str(len(found) + 1), name, amount, None, doc, table.page_number, yearly
            )
            if line:
                line["part"] = cell(part) or line["part"]
                found.append(line)
    return found


def rfq_lines(
    manifest: PackageManifest, parsed: Mapping[str, ParsedFile], canonical: Mapping[str, Path]
) -> dict:
    """The ``lines`` object of the pre-cost setup: price-sheet rows of every file, in file order."""
    rows: list[dict] = []
    for file in manifest.files:
        if file.kind == "xlsx" and file.id in parsed:
            rows.extend(_from_tables(file.id, parsed[file.id].tables))
        elif file.id in canonical:
            rows.extend(_from_pdf(file.id, canonical[file.id]))
    # A revised sheet (an addendum) lists a group again: its last listing replaces the earlier ones.
    last_run = {row["group"]: row.get("run") for row in rows if row["group"]}
    latest: dict[tuple, dict] = {}
    for row in rows:
        if row["group"] and row.get("run") != last_run[row["group"]]:
            continue
        key = (row["group"], row["no"]) if row["group"] else (row["source_doc"], row["no"], row["part"])
        latest.pop(key, None)
        latest[key] = {k: v for k, v in row.items() if k != "run"}
    found = list(latest.values())
    return {"items": found[:MAX_LINES], "total": len(found), "limit": MAX_LINES}


__all__ = ["MAX_LINES", "rfq_lines"]
