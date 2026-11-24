"""Line items of an RFQ's price sheet (bid form), for pricing line by line.

A price sheet lists what is bought: item number, part number, description and an
estimated quantity, with blank price cells for the supplier. Layouts differ per
customer, so every file is first turned into tables of cells:

- a PDF page (a Word file is read through its canonical PDF) gives the tables PyMuPDF
  finds from ruling lines; the words inside each cell are read from the page, so the
  text keeps its spaces. A page without ruled tables but with a header row naming a
  quantity and a description is read by text alignment instead.
- a spreadsheet gives the tables of the parsed workbook.

A table is a price sheet when one of its first rows is a header in which the words of
``data/rules/price_sheet.yaml`` (plus header words a user approved) name a quantity
column and a description or part-number column, or when that header row has the same
cells as one a user already assigned by hand (a remembered layout). A table without a
header that continues the previous one on the next page keeps its columns.

Only numbers printed on the sheet are taken: nothing is estimated, and a row without a
positive whole quantity is not a line item.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pymupdf

from autorfx.consistency.normalize import parse_number
from autorfx.models import PackageManifest, ParsedFile, Table
from autorfx.rules import COLUMN_ROLES, PriceSheetRules

MAX_LINES = 200
MAX_TABLES = 40
"""Tables listed for the user to check or assign; a long RFQ has many small tables."""
SAMPLE_ROWS = 8
HEADER_SCAN = 6
"""Rows at the top of a table that may be its header (titles and notes come first)."""
CELL_CHARS = 80
PART = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9./-]{3,}$")
INTEGER = re.compile(r"^\s*[$€£]?\s*\d[\d,.]*\s*$")


@dataclass(frozen=True)
class Grid:
    """A table of one file: rows of cell texts, the page of each row."""

    id: str
    source_doc: str
    title: str
    rows: tuple[tuple[str, ...], ...]
    pages: tuple[int, ...]
    spans: tuple[bool, ...] = ()
    """Per row: True when one cell spans the row (a group heading of a ruled table)."""


@dataclass
class Reading:
    """How a grid was read: its header row and the role of each column."""

    grid: Grid
    header: int | None = None
    columns: dict[str, int] = field(default_factory=dict)
    how: str = "none"
    """"rules", "layout" (a layout a user assigned), "continued" or "none"."""
    lines: list[dict] = field(default_factory=list)


def normalize(text: str) -> str:
    return " ".join(text.split()).strip()


def signature(cells: Sequence[str]) -> str:
    """Key of a header row: a layout a user assigned is found again by it."""
    return "|".join(normalize(c).lower() for c in cells)


# ── PDF ─────────────────────────────────────────────────────────────────────
def _join(words: Sequence[tuple]) -> str:
    """Words of a cell in reading order; a part number broken after a hyphen is joined again."""
    text = ""
    for w in sorted(words, key=lambda w: (round((w[1] + w[3]) / 2 / 3), w[0])):
        broken = text.endswith(("-", "/")) and not text.endswith((" -", " /"))
        text += w[4] if not text or broken else " " + w[4]
    return normalize(text)[:500]


def _cell_text(words: Sequence[tuple], rect: Sequence[float]) -> str:
    x0, y0, x1, y1 = rect
    return _join([w for w in words if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1])


def _spill(words: Sequence[tuple], columns: Sequence[tuple[float, float]], top: float, bottom: float) -> list:
    """Rows above the first ruled table of a page, read by the columns of the previous page's table.

    A sheet that breaks over a page sometimes loses its ruling lines at the top of the next page.
    A row starts on a line with text in at least two columns; other lines (a wrapped description,
    printed above or below the middle of its row) join the nearest row.
    """
    height = max((w[3] - w[1] for w in words), default=10)
    lines: dict[int, list[tuple]] = {}
    for w in words:
        middle = (w[1] + w[3]) / 2
        if top < middle < bottom and columns[0][0] - 2 <= (w[0] + w[2]) / 2 <= columns[-1][1] + 2:
            lines.setdefault(round(middle / 3), []).append(w)

    def column(w: tuple) -> int | None:
        x = (w[0] + w[2]) / 2
        return next((i for i, (a, b) in enumerate(columns) if a <= x <= b), None)

    def split(ws: list[tuple]) -> list[list[tuple]]:
        by_column: list[list[tuple]] = [[] for _ in columns]
        for w in ws:
            if (i := column(w)) is not None:
                by_column[i].append(w)
        return by_column

    # A row has text in two columns or more, one of them a whole number (an item number or a quantity).
    anchors = []
    for k, ws in sorted(lines.items()):
        parts = split(ws)
        texts = [_join(p) for p in parts]
        if sum(1 for t in texts if t) >= 2 and any(INTEGER.match(t) for t in texts if t):
            anchors.append((k, parts))
    if not anchors:
        return []
    cells = [[list(p) for p in parts] for _, parts in anchors]
    for k, ws in lines.items():
        if any(k == a for a, _ in anchors):
            continue
        nearest = min(range(len(anchors)), key=lambda i: abs(anchors[i][0] - k))
        if abs(anchors[nearest][0] - k) * 3 > 1.5 * height:
            continue  # a page header or footer, not part of the sheet
        for i, part in enumerate(split(ws)):
            own = _join(anchors[nearest][1][i])
            # A wrapped description continues a text cell; a subtotal line must not fill a number cell.
            if part and (not own or any(c.isalpha() for c in own)):
                cells[nearest][i].extend(part)
    return [tuple(_join(c) for c in row) for row in cells]


def _header_line(words: Sequence[tuple], compiled: Mapping[str, tuple]) -> bool:
    """A rendered line naming a quantity and a description: a price sheet without ruling lines."""
    lines: dict[int, list[str]] = {}
    for w in words:
        lines.setdefault(round((w[1] + w[3]) / 2 / 3), []).append(w[4])
    for texts in lines.values():
        text = " ".join(texts)
        if len(text) <= 200 and _hits(text, compiled["quantity"]) and _hits(text, compiled["description"]):
            return True
    return False


@lru_cache(maxsize=16)
def _pdf_grids(
    doc: str, path: str, _stamp: tuple[int, int], quantity: str, description: str
) -> tuple[Grid, ...]:
    compiled = {
        "quantity": ((re.compile(quantity, re.IGNORECASE), 1),),
        "description": ((re.compile(description, re.IGNORECASE), 1),),
    }
    grids: list[Grid] = []
    try:
        pdf = pymupdf.open(path)
    except Exception:  # PyMuPDF raises several unrelated types for a damaged file
        return ()
    with pdf:
        last: list[tuple[float, float]] | None = None
        """Column x ranges of the last table on the previous page."""
        for page in pdf:
            words = page.get_text("words")
            if not words:
                last = None
                continue
            tables = page.find_tables().tables
            if not tables and _header_line(words, compiled):
                tables = page.find_tables(strategy="text").tables
            if last:
                bottom = tables[0].bbox[1] if tables else page.rect.height
                spilled = _spill(words, last, page.rect.height * 0.06, bottom)
                if spilled:
                    grids.append(
                        Grid(
                            id=f"{doc}:p{page.number + 1}:t0",
                            source_doc=doc,
                            title=f"{page.number + 1}쪽 위 (앞 쪽 표에 이어짐)",
                            rows=tuple(spilled),
                            pages=tuple(page.number + 1 for _ in spilled),
                        )
                    )
            last = None
            for n, table in enumerate(tables, start=1):
                rows: list[tuple[str, ...]] = []
                spans: list[bool] = []
                for row in table.rows:
                    cells = [_cell_text(words, c) if c else "" for c in row.cells]
                    if not any(cells):
                        continue
                    rows.append(tuple(cells))
                    spans.append(sum(1 for c in row.cells if c is not None) == 1 and len(row.cells) > 1)
                widest = max(table.rows, key=lambda r: sum(1 for c in r.cells if c))
                if all(widest.cells):
                    last = [(c[0], c[2]) for c in widest.cells]
                if rows:
                    grids.append(
                        Grid(
                            id=f"{doc}:p{page.number + 1}:t{n}",
                            source_doc=doc,
                            title=f"{page.number + 1}쪽 표 {n}",
                            rows=tuple(rows),
                            pages=tuple(page.number + 1 for _ in rows),
                            spans=tuple(spans),
                        )
                    )
    return tuple(grids)


def pdf_grids(doc: str, path: Path, rules: PriceSheetRules) -> tuple[Grid, ...]:
    """Tables of a PDF, read once per file version."""
    if not path.is_file():
        return ()
    stat = path.stat()
    # Only the two header vocabularies decide the text-alignment fallback; they key the cache.
    compiled = rules.compiled()
    quantity, description = (
        "|".join(f"(?:{p.pattern})" for p, _ in compiled[r]) for r in ("quantity", "description")
    )
    return _pdf_grids(doc, str(path), (stat.st_mtime_ns, stat.st_size), quantity, description)


def table_grids(doc: str, tables: Iterable[Table]) -> tuple[Grid, ...]:
    """Tables of a parsed spreadsheet; the parser's header row is the first row again."""
    grids = []
    for n, table in enumerate(tables, start=1):
        rows = ((table.header,) if table.header else ()) + tuple(table.rows)
        if rows:
            grids.append(
                Grid(
                    id=f"{doc}:s{n}",
                    source_doc=doc,
                    title=f"시트 {table.title}",
                    rows=tuple(tuple(normalize(c) for c in r) for r in rows),
                    pages=tuple(table.page_number for _ in rows),
                )
            )
    return tuple(grids)


# ── reading ─────────────────────────────────────────────────────────────────
def _hits(text: str, patterns: Iterable[tuple[re.Pattern[str], int]]) -> int:
    """Weight of the longest keyword found in ``text`` (0 when none)."""
    return max((weight for pattern, weight in patterns if pattern.search(text)), default=0)


PREFERENCE = ("description", "quantity", "part", "no", "price", "total", "unit", "months")
"""Between equally long keywords ("Item" may be a number or a name), the earlier role wins."""


def header_roles(cells: Sequence[str], compiled: Mapping[str, tuple]) -> dict[str, int]:
    """Role -> column for a header row, the longest keyword match first.

    "Item | Description | Qty": "Description" names the description, so "Item" is the number.
    Each role goes to one column and each column to one role; between equal matches the
    left column wins.
    """
    pairs = []
    for index, cell in enumerate(cells):
        text = normalize(cell)
        if text and len(text) <= 80:
            pairs += [(_hits(text, compiled[role]), role, index) for role in COLUMN_ROLES]
    pairs.sort(key=lambda p: (-p[0], PREFERENCE.index(p[1]), p[2]))
    found: dict[str, int] = {}
    for score, role, index in pairs:
        if score and role not in found and index not in found.values():
            found[role] = index
    return found


def _readable(columns: Mapping[str, int]) -> bool:
    return "quantity" in columns and ("description" in columns or "part" in columns)


def _cell(row: Sequence[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def _group(text: str, compiled: Mapping[str, tuple]) -> str | None:
    for pattern, _ in compiled["group"]:
        if match := pattern.match(text):
            return normalize(match.group(1) if match.groups() else text)[:80]
    return None


def _read_rows(reading: Reading, start: int, state: dict, compiled: Mapping[str, tuple]) -> None:
    grid, columns = reading.grid, reading.columns
    header = grid.rows[reading.header] if reading.header is not None else state["header"]
    quantity_head = _cell(header, columns.get("quantity"))
    months = columns.get("months")
    monthly = months is not None and bool(_hits(quantity_head, compiled["monthly"]))
    yearly = bool(_hits(quantity_head, compiled["yearly"])) or months is not None
    for index in range(start, len(grid.rows)):
        row = grid.rows[index]
        filled = [c for c in row if c]
        text = normalize(" ".join(filled))
        heading = _group(text, compiled)
        spans = grid.spans[index] if grid.spans else False
        if _hits(text, compiled["total_row"]) and not INTEGER.match(_cell(row, columns.get("no")) or "x"):
            continue  # "Category 1 – Brakes Subtotal: $"
        if heading or (spans and len(filled) == 1 and not INTEGER.match(filled[0])):
            state["group"] = heading or text[:80]
            state["run"] += 1
            continue
        amount = parse_number(_cell(row, columns["quantity"]).strip(" $€£"), "en")
        if monthly and amount is not None:
            count = parse_number(_cell(row, months), "en")
            amount = amount * count if count is not None else None
        name = _cell(row, columns.get("description"))
        part = _cell(row, columns.get("part")) or None
        if (
            amount is None
            and name
            and state["last_at"] == (grid.id, index - 1)
            and not _cell(row, columns.get("no"))
        ):
            # A description that wrapped onto the next row of a table read by text alignment.
            state["last"]["name"] = normalize(f"{state['last']['name']} {name}")[:200]
            state["last_at"] = (grid.id, index)
            continue
        if amount is None or amount <= 0 or amount != int(amount):
            continue
        if part is None and name:
            first, _, rest = name.partition(" ")
            if PART.match(first) and rest:
                part, name = first, rest
        name = normalize(name or part or "")
        if len(name) < 2 or not any(c.isalpha() for c in name):
            continue
        state["count"] += 1
        line = {
            "no": _cell(row, columns.get("no")) or str(state["count"]),
            "part": part,
            "name": name[:200],
            "quantity": int(amount),
            "yearly": yearly,
            "group": state["group"],
            "source_doc": grid.source_doc,
            "page": grid.pages[index],
            "table": grid.id,
            "run": (grid.source_doc, state["run"]),
        }
        reading.lines.append(line)
        state["last"], state["last_at"] = line, (grid.id, index)


def read_grids(
    grids: Sequence[Grid], compiled: Mapping[str, tuple], layouts: Mapping[str, Mapping[str, int]]
) -> list[Reading]:
    """Read every grid of one file in order; a headerless grid may continue the previous one."""
    readings: list[Reading] = []
    state: dict = {
        "group": None,
        "run": 0,
        "count": 0,
        "last": None,
        "last_at": None,
        "header": None,
        "columns": None,
    }
    for grid in grids:
        reading = Reading(grid)
        for index, row in enumerate(grid.rows[:HEADER_SCAN]):
            if (layout := layouts.get(signature(row))) is not None:
                reading.header, reading.columns, reading.how = index, dict(layout), "layout"
                break
            roles = header_roles(row, compiled)
            if _readable(roles):
                reading.header, reading.columns, reading.how = index, roles, "rules"
                break
        if reading.header is not None:
            state.update(header=grid.rows[reading.header], columns=reading.columns, last=None)
            if reading.header > 0 and (heading := _group(normalize(" ".join(grid.rows[0])), compiled)):
                state["group"], state["run"] = heading, state["run"] + 1
            _read_rows(reading, reading.header + 1, state, compiled)
        elif state["columns"] is not None and len(grid.rows[0]) == len(state["header"]):
            # The sheet goes on over a page break without repeating its header.
            reading.columns, reading.how = dict(state["columns"]), "continued"
            _read_rows(reading, 0, state, compiled)
        else:
            state.update(header=None, columns=None, last=None)
        readings.append(reading)
    return readings


def _looks_like_table(grid: Grid) -> bool:
    """Worth showing for assignment: a few rows, several columns, whole numbers in some cells."""
    if len(grid.rows) < 3 or max(len(r) for r in grid.rows) < 2:
        return False
    numbered = sum(1 for r in grid.rows if any(INTEGER.match(c) for c in r if c))
    return numbered >= 2


def _summary(reading: Reading) -> dict:
    grid = reading.grid
    header = reading.header if reading.header is not None else _guess_header(grid)
    return {
        "id": grid.id,
        "source_doc": grid.source_doc,
        "page": grid.pages[0],
        "title": grid.title,
        "header_row": header,
        "rows": [[c[:CELL_CHARS] for c in row] for row in grid.rows[: max(SAMPLE_ROWS, (header or 0) + 4)]],
        "columns": dict(reading.columns),
        "how": reading.how,
        "lines": len(reading.lines),
    }


def _guess_header(grid: Grid) -> int:
    """The first row of several text cells and no whole numbers; else the first row."""
    for index, row in enumerate(grid.rows[:HEADER_SCAN]):
        texts = [c for c in row if c]
        if len(texts) >= 2 and not any(INTEGER.match(c) for c in texts):
            return index
    return 0


def file_grids(
    manifest: PackageManifest,
    parsed: Mapping[str, ParsedFile],
    canonical: Mapping[str, Path],
    rules: PriceSheetRules,
) -> list[tuple[Grid, ...]]:
    """Tables of every file, in file order."""
    found = []
    for file in manifest.files:
        if file.kind == "xlsx" and file.id in parsed:
            found.append(table_grids(file.id, parsed[file.id].tables))
        elif file.id in canonical:
            found.append(pdf_grids(file.id, canonical[file.id], rules))
    return found


def rfq_lines(
    manifest: PackageManifest,
    parsed: Mapping[str, ParsedFile],
    canonical: Mapping[str, Path],
    rules: PriceSheetRules,
    *,
    words: Mapping[str, Sequence[str]] | None = None,
    layouts: Mapping[str, Mapping[str, int]] | None = None,
) -> dict:
    """The ``lines`` object of the pre-cost setup: price-sheet rows of every file, and the tables seen.

    ``words`` are header words a user approved per role; ``layouts`` maps a header signature to the
    columns a user assigned.
    """
    compiled = rules.compiled({role: tuple(ws) for role, ws in (words or {}).items()})
    readings: list[Reading] = []
    for grids in file_grids(manifest, parsed, canonical, rules):
        readings.extend(read_grids(grids, compiled, layouts or {}))
    rows = [line for reading in readings for line in reading.lines]
    # A revised sheet (an addendum) lists a group again: its last listing replaces the earlier ones.
    last_run = {row["group"]: row["run"] for row in rows if row["group"]}
    latest: dict[tuple, dict] = {}
    for row in rows:
        if row["group"] and row["run"] != last_run[row["group"]]:
            continue
        key = (
            (row["group"], row["no"])
            if row["group"]
            else (row["source_doc"], row["table"], row["no"], row["part"])
        )
        latest.pop(key, None)
        latest[key] = {k: v for k, v in row.items() if k != "run"}
    found = list(latest.values())
    tables = [r for r in readings if r.lines or _looks_like_table(r.grid)]
    return {
        "items": found[:MAX_LINES],
        "total": len(found),
        "limit": MAX_LINES,
        "tables": [_summary(r) for r in tables[:MAX_TABLES]],
    }


def find_grid(
    manifest: PackageManifest,
    parsed: Mapping[str, ParsedFile],
    canonical: Mapping[str, Path],
    rules: PriceSheetRules,
    grid_id: str,
) -> Grid | None:
    return next(
        (g for grids in file_grids(manifest, parsed, canonical, rules) for g in grids if g.id == grid_id),
        None,
    )


__all__ = ["MAX_LINES", "Grid", "find_grid", "header_roles", "rfq_lines", "signature"]
