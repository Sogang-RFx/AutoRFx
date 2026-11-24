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
positive whole quantity is not a line item. The exception is an order form whose quantity
column is blank in every row (the buyer fills it in): its priced rows are items without a
quantity, for the user to fill in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pymupdf

from autorfx.consistency.normalize import parse_number
from autorfx.models import FileLayout, PackageManifest, ParsedFile, Table
from autorfx.rules import COLUMN_ROLES, PriceSheetRules

MAX_LINES = 200
"""Lines the screen fills at most, from the lists the user picked."""
MAX_ITEMS = 3000
"""Lines sent for every list together; the user picks the lists and the groups to fill."""
MAX_LISTS = 40
MAX_TABLES = 40
"""Tables listed for the user to check or assign; a long RFQ has many small tables."""
SAMPLE_ROWS = 8
HEADER_SCAN = 6
"""Rows at the top of a table that may be its header (titles and notes come first)."""
LAYOUT_SCAN = 40
"""Rows at the top of a table searched for a header a user (or an AI suggestion they accepted) assigned.
A sheet may start with a page of notes ("Annexure M": the header is on row 28)."""
CELL_CHARS = 80
HEADER_CHARS = 120
"""A header cell longer than this is a sentence, not a column name."""
CAPTION = re.compile(
    r"^\s*(?:annex|appendix|attachment|exhibit|schedule|lot|anlage|anhang|별첨|첨부|부록)\b", re.IGNORECASE
)
"""A caption that names a part of the RFQ ("Annex 2")."""
SUBCAPTION = re.compile(r"^\s*(?:table|tabelle|form|price|bid|표|양식)\b", re.IGNORECASE)
"""A caption that names the table itself ("TABLE 1: Offer to Supply Goods")."""
PART = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9./-]{3,}$")
COUNT = re.compile(r"^\s*(\d[\d,]*(?:\.\d+)?)\s*[^\W\d_][^\d]{0,14}$")
"""A quantity with its unit ("1 pc.", "2 units", "3 sets", "10 Stk.")."""
INTEGER = re.compile(r"^\s*[$€£]?\s*\d[\d,.]*\s*$")


@dataclass(frozen=True)
class Grid:
    """A table of one file: rows of cell texts, the page of each row."""

    id: str
    source_doc: str
    title: str
    rows: tuple[tuple[str, ...], ...]
    pages: tuple[int, ...]
    spans: tuple[int | None, ...] = ()
    """Per row: the column of the one cell that spans the row (a heading or a note), else None."""
    boxes: tuple[tuple[int, float, float, float, float], ...] = ()
    """Where the table is printed: (page, x, y, w, h), as fractions of the page."""
    caption: str = ""
    """Text printed just above the table ("Annex 2", "TABLE 1: Offer to Supply Goods")."""
    part: bool = False
    """One of several tables cut from one sheet: a list of its own, even with the same columns."""
    sheet: bool = False
    """Read from a spreadsheet: every value sits in the cell under its header, nothing is off by one."""
    xs: tuple[tuple[float, float], ...] = ()
    """Per column of a PDF table: its left and right edge on the page, in points (empty when unknown).
    A table going on over the next page may be cut into other columns there; the edges match them."""


@dataclass
class Reading:
    """How a grid was read: its header row and the role of each column."""

    grid: Grid
    header: int | None = None
    columns: dict[str, int] = field(default_factory=dict)
    how: str = "none"
    """"rules", "layout" (a layout a user assigned), "continued" or "none"."""
    lines: list[dict] = field(default_factory=list)
    priced: bool = False
    """A price or total column is named in or just around the header (a header of several rows)."""
    open: bool = False
    """An order form: the quantity column is left for the buyer, so items are read without one."""


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


def _spill(
    words: Sequence[tuple], columns: Sequence[tuple[float, float]], top: float, bottom: float
) -> tuple[list[tuple[str, ...]], tuple[float, float, float, float] | None]:
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
        return [], None
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
    used = [w for row in cells for c in row for w in c]
    box = (
        min(w[0] for w in used),
        min(w[1] for w in used),
        max(w[2] for w in used),
        max(w[3] for w in used),
    )
    return [tuple(_join(c) for c in row) for row in cells], box


def _caption(page: pymupdf.Page, bbox: Sequence[float]) -> str:
    """Titles printed above a table: the nearest lines that read like a caption ("Annex 2")."""
    x0, top, x1, _ = bbox
    above = []
    for block in page.get_text("blocks"):
        bx0, _, bx1, by1, text = block[:5]
        if top - 300 < by1 <= top + 2 and bx1 > x0 and bx0 < x1:
            first = normalize(text.split("\n", 1)[0])
            if first:
                above.append((by1, first))
    above.sort(reverse=True)
    named = next((text for _, text in above if CAPTION.match(text)), None)
    sub = next((text for _, text in above if SUBCAPTION.match(text)), None)
    found = [t for t in (named, sub) if t]
    if found:
        return " · ".join(found)[:120]
    return above[0][1][:80] if above and len(above[0][1]) <= 80 else ""


def _box(page: pymupdf.Page, number: int, bbox: Sequence[float]) -> tuple[int, float, float, float, float]:
    """Normalised page box clamped to ``[0, 1]`` so schema validation never sees overflow."""
    w, h = page.rect.width or 1, page.rect.height or 1
    x0 = min(max(0.0, bbox[0]), w)
    y0 = min(max(0.0, bbox[1]), h)
    x1 = min(max(x0, bbox[2]), w)
    y1 = min(max(y0, bbox[3]), h)
    return (
        number,
        round(x0 / w, 4),
        round(y0 / h, 4),
        round((x1 - x0) / w, 4),
        round((y1 - y0) / h, 4),
    )


RULE = 1.5
"""A drawn rectangle this thin (in points) is a ruling line; a wider filled one is shading."""
SNAP = 2.0
"""Ruling lines closer than this are the same line."""
MARGIN = 12.0
"""PyMuPDF's table box can stop inside the outer border; lines this far outside it still count."""

Cell = tuple[float, float, float, float]


def _rulings(page: pymupdf.Page) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Horizontal (y, x0, x1) and vertical (x, y0, y1) ruling lines drawn on a page.

    Lines, thin filled rectangles and the edges of stroked rectangles count; filled shading does
    not. A word processor often shades each line of text in a cell, and those edges are not cells.
    """
    across: list[tuple[float, float, float]] = []
    down: list[tuple[float, float, float]] = []

    def add(r: pymupdf.Rect) -> None:
        if r.height <= RULE and r.width > RULE:
            across.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
        elif r.width <= RULE and r.height > RULE:
            down.append(((r.x0 + r.x1) / 2, r.y0, r.y1))

    for drawing in page.get_drawings():
        stroked = drawing.get("color") is not None and drawing.get("type") in ("s", "fs")
        for item in drawing["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
                add(pymupdf.Rect(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)))
            elif item[0] == "re":
                r = pymupdf.Rect(item[1])
                if min(r.width, r.height) <= RULE:
                    add(r)
                elif stroked:
                    for edge in (
                        (r.x0, r.y0, r.x1, r.y0),
                        (r.x0, r.y1, r.x1, r.y1),
                        (r.x0, r.y0, r.x0, r.y1),
                        (r.x1, r.y0, r.x1, r.y1),
                    ):
                        add(pymupdf.Rect(*edge))
    return across, down


def _snap(values: Iterable[float]) -> list[float]:
    found: list[float] = []
    for v in sorted(values):
        if not found or v - found[-1] > SNAP:
            found.append(v)
    return found


def _covered(lines: Sequence[tuple[float, float, float]], at: float, start: float, end: float) -> bool:
    """A ruling line at ``at`` runs along most of ``start``..``end``."""
    need = (end - start) * 0.6
    run = sum(max(0.0, min(b, end) - max(a, start)) for pos, a, b in lines if abs(pos - at) <= SNAP)
    return run >= need


def _ruled_cells(
    rulings: tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]],
    bbox: Sequence[float],
) -> list[list[Cell | None]] | None:
    """Cells of a table as its ruling lines draw them; a merged cell is one cell over its rows.

    Rows of cells, as PyMuPDF gives them: a merged cell sits at its top-left position and the
    positions it covers are None. None when the lines do not draw a grid (an unruled table).
    """
    x0, y0, x1, y1 = bbox

    def inside(pos: float, a: float, b: float, lo: float, hi: float, l2: float, h2: float) -> bool:
        return lo - MARGIN <= pos <= hi + MARGIN and b > l2 - MARGIN and a < h2 + MARGIN

    across = [(y, a, b) for y, a, b in rulings[0] if inside(y, a, b, y0, y1, x0, x1) and b - a > SNAP]
    down = [(x, a, b) for x, a, b in rulings[1] if inside(x, a, b, x0, x1, y0, y1) and b - a > SNAP]
    xs, ys = _snap(x for x, _, _ in down), _snap(y for y, _, _ in across)
    if len(xs) < 3 or len(ys) < 3:
        return None
    n_rows, n_cols = len(ys) - 1, len(xs) - 1
    taken = [[False] * n_cols for _ in range(n_rows)]
    rows: list[list[Cell | None]] = [[None] * n_cols for _ in range(n_rows)]
    for r in range(n_rows):
        for c in range(n_cols):
            if taken[r][c]:
                continue
            right = c
            while right + 1 < n_cols and not _covered(down, xs[right + 1], ys[r], ys[r + 1]):
                right += 1
            bottom = r
            while bottom + 1 < n_rows and not any(
                _covered(across, ys[bottom + 1], xs[k], xs[k + 1]) for k in range(c, right + 1)
            ):
                bottom += 1
            for rr in range(r, bottom + 1):
                for cc in range(c, right + 1):
                    taken[rr][cc] = True
            rows[r][c] = (xs[c], ys[r], xs[right + 1], ys[bottom + 1])
    # A column that is a cell of its own in no row is a sliver between lines: nothing to show.
    keep = [c for c in range(n_cols) if any(row[c] is not None for row in rows)]
    return [[row[c] for c in keep] for row in rows]


def _column_edges(cells: Sequence[Sequence[Cell | None]]) -> tuple[tuple[float, float], ...]:
    """Left and right edge of each column: the narrowest cell that starts in it (a merged cell is wider).

    Empty when a column has no cell of its own in any row (its edges are unknown).
    """
    width = max((len(row) for row in cells), default=0)
    edges = []
    for c in range(width):
        own = [row[c] for row in cells if c < len(row) and row[c] is not None]
        if not own:
            return ()
        edges.append((min(cell[0] for cell in own), min(cell[2] for cell in own)))
    return tuple(edges)


def _matched_columns(
    columns: Mapping[str, int], before: Sequence[tuple[float, float]], after: Sequence[tuple[float, float]]
) -> dict[str, int] | None:
    """The roles of a table's columns on the next page, matched by where the columns are printed.

    Each role goes to the column that shares most of its width with the role's column before;
    None when a role has no such column or two roles land in one column.
    """
    found: dict[str, int] = {}
    for role, index in columns.items():
        if index >= len(before):
            return None
        a, b = before[index]
        overlaps = [(min(b, d) - max(a, c), k) for k, (c, d) in enumerate(after)]
        share, best = max(overlaps, default=(0.0, -1))
        if best < 0 or share < 0.5 * min(b - a, after[best][1] - after[best][0]):
            return None
        found[role] = best
    return found if len(set(found.values())) == len(found) else None


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
            rulings = _rulings(page)
            if not words:
                last = None
                continue
            tables = page.find_tables().tables
            if not tables and _header_line(words, compiled):
                tables = page.find_tables(strategy="text").tables
            if last:
                bottom = tables[0].bbox[1] if tables else page.rect.height
                spilled, box = _spill(words, last, page.rect.height * 0.06, bottom)
                if spilled and box:
                    grids.append(
                        Grid(
                            id=f"{doc}:p{page.number + 1}:t0",
                            source_doc=doc,
                            title=f"{page.number + 1}쪽 위 (앞 쪽 표에 이어짐)",
                            rows=tuple(spilled),
                            pages=tuple(page.number + 1 for _ in spilled),
                            boxes=(_box(page, page.number + 1, box),),
                            xs=tuple(last),
                        )
                    )
            last = None
            for n, table in enumerate(tables, start=1):
                # The cells as the PDF draws them, when it rules the table; else PyMuPDF's.
                grid = _ruled_cells(rulings, table.bbox) or [list(row.cells) for row in table.rows]
                rows: list[tuple[str, ...]] = []
                spans: list[int | None] = []
                for row_cells in grid:
                    cells = [_cell_text(words, c) if c else "" for c in row_cells]
                    if not any(cells):
                        continue
                    rows.append(tuple(cells))
                    present = [i for i, c in enumerate(row_cells) if c is not None]
                    spans.append(present[0] if len(present) == 1 and len(row_cells) > 1 else None)
                widest = max(grid, key=lambda r: sum(1 for c in r if c))
                if all(widest):
                    last = [(c[0], c[2]) for c in widest if c]
                xs = _column_edges(grid)
                if rows:
                    grids.append(
                        Grid(
                            id=f"{doc}:p{page.number + 1}:t{n}",
                            source_doc=doc,
                            title=f"{page.number + 1}쪽 표 {n}",
                            rows=tuple(rows),
                            pages=tuple(page.number + 1 for _ in rows),
                            spans=tuple(spans),
                            boxes=(_box(page, page.number + 1, table.bbox),),
                            caption=_caption(page, table.bbox),
                            xs=xs,
                        )
                    )
    return tuple(grids)


def pdf_grids(
    doc: str,
    path: Path,
    rules: PriceSheetRules,
    words: Mapping[str, Sequence[str]] | None = None,
) -> tuple[Grid, ...]:
    """Tables of a PDF, read once per file version.

    ``words`` are approved header phrases (per role). They join the rules for the
    text-alignment fallback so a line-less table is found after those words are approved.
    """
    if not path.is_file():
        return ()
    stat = path.stat()
    # Quantity and description vocabularies decide the text-alignment fallback; they key the cache.
    compiled = rules.compiled({role: tuple(ws) for role, ws in (words or {}).items()})
    quantity, description = (
        "|".join(f"(?:{p.pattern})" for p, _ in compiled[r]) for r in ("quantity", "description")
    )
    return _pdf_grids(doc, str(path), (stat.st_mtime_ns, stat.st_size), quantity, description)


def table_grids(
    doc: str,
    tables: Iterable[Table],
    compiled: Mapping[str, tuple] | None = None,
    rendered: FileLayout | None = None,
) -> tuple[Grid, ...]:
    """Sheets of a parsed spreadsheet, each read as one table, then cut at its header rows.

    The parser splits a sheet into blocks for its text (at blank rows, at a category title over
    the next rows), each trimmed to the columns it uses. A price sheet goes on through those
    blocks, so the blocks of one sheet are put back together, every cell in its own sheet column.

    An order form puts several tables on one sheet, one under the other: contact fields, then
    "Base Vehicles", "Contract Options", ... each with its own header row. With ``compiled``
    rules the sheet is cut at every header row below the first rows, so each part is read as
    a table of its own, named by the title row just above its header. ``rendered`` (where each
    sheet row is printed in the canonical PDF) gives the boxes that show the table in the source.
    """
    printed = {(r.sheet, r.row_index): r for r in rendered.rows} if rendered else {}
    sheets: dict[str, list[Table]] = {}
    for table in tables:
        sheets.setdefault(table.section_id, []).append(table)
    grids = []
    for n, (sheet, blocks) in enumerate(sheets.items(), start=1):
        placed = [row for table in blocks for row in _placed_rows(table)]
        if not placed:
            continue
        width = max(at + len(cells) for _, _, at, cells in placed)
        rows = tuple(
            tuple(("",) * at + tuple(normalize(c) for c in cells) + ("",) * (width - at - len(cells)))
            for _, _, at, cells in placed
        )
        numbers = [number for number, _, _, _ in placed]
        pages = tuple(page for _, page, _, _ in placed)
        cuts: list[tuple[int, dict[str, int]]] = [(0, {})]
        if compiled is not None:
            for index in range(HEADER_SCAN, len(rows)):
                if index - cuts[-1][0] >= HEADER_SCAN and (roles := _header_roles_at(rows, index, compiled)):
                    cuts.append((index, roles))
        title_of = blocks[0].title
        for (start, roles), end in zip(cuts, [c for c, _ in cuts[1:]] + [len(rows)], strict=True):
            # One table under the sheet's notes is named by its sheet: the line above its header
            # is then a note ("Contract Effective until August 31, 2027"), not a table title.
            title = _sheet_title(rows[start - 1], roles) if start and len(cuts) > 2 else ""
            grids.append(
                Grid(
                    id=f"{doc}:s{n}" if not start else f"{doc}:s{n}:r{numbers[start] or start + 1}",
                    source_doc=doc,
                    title=f"시트 {title_of}" + (f" · {title}" if title else ""),
                    caption=title or title_of,
                    rows=rows[start:end],
                    pages=pages[start:end],
                    part=len(cuts) > 1,
                    sheet=True,
                    boxes=_sheet_boxes(
                        [printed.get((sheet, number)) for number in numbers[start:end] if number]
                    ),
                )
            )
    return tuple(grids)


def _placed_rows(table: Table) -> list[tuple[int | None, int, int, tuple[str, ...]]]:
    """Rows of one parsed block: (sheet row, page, first sheet column, cells).

    The parser's header row is the first row again, on the sheet row above the first data row.
    The first column comes from the cell anchors ("Sheet!B73" in the second cell: column A).
    """
    at = 0
    for anchors in table.cell_anchors:
        found = next(((i, a) for i, a in enumerate(anchors) if a and SHEET_COLUMN.search(a)), None)
        if found is not None:
            index, anchor = found
            at = max(0, _column_number(SHEET_COLUMN.search(anchor).group(1)) - index)
            break
    numbers = [_sheet_row(locus.anchor) for locus in table.row_loci]
    pages = [locus.page_number for locus in table.row_loci]
    if len(numbers) != len(table.rows):
        numbers, pages = [None] * len(table.rows), [table.page_number] * len(table.rows)
    anchors = table.cell_anchors if len(table.cell_anchors) == len(table.rows) else [()] * len(table.rows)
    rows = [(numbers[i], pages[i], *_anchored(r, anchors[i], at)) for i, r in enumerate(table.rows)]
    if table.header:
        first = numbers[0] - 1 if numbers and numbers[0] else None
        rows.insert(0, (first, table.page_number, at, tuple(table.header)))
    return rows


def _anchored(cells: Sequence[str], anchors: Sequence[str | None], at: int) -> tuple[int, tuple[str, ...]]:
    """(first column, cells) of one row, each cell at the column of its own anchor.

    The parser leaves out empty columns inside a row ("Item | (blank) | 1300 | 0" for A, E and
    F), so a block's first column alone would shift the price under the quantity header.
    """
    if not any(a and SHEET_COLUMN.search(a) for a in anchors):
        return at, tuple(cells)
    placed: dict[int, str] = {}
    column = at - 1
    for index, cell in enumerate(cells):
        anchor = anchors[index] if index < len(anchors) else None
        found = SHEET_COLUMN.search(anchor) if anchor else None
        column = _column_number(found.group(1)) if found else column + 1
        if cell:
            placed.setdefault(column, cell)
    if not placed:
        return at, tuple(cells)
    width = max(placed) + 1
    return 0, tuple(placed.get(i, "") for i in range(width))


SHEET_COLUMN = re.compile(r"!\$?([A-Z]+)\$?\d+")


def _column_number(letters: str) -> int:
    """0-based sheet column of its letters ("A" -> 0, "AB" -> 27)."""
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number - 1


SHEET_ROW = re.compile(r"!\$?[A-Z]+\$?(\d+)")


def _sheet_row(anchor: str | None) -> int | None:
    """The row number of a cell anchor ("TESCO Order Form!B16:D16" -> 16)."""
    match = SHEET_ROW.search(anchor or "")
    return int(match.group(1)) if match else None


def _sheet_boxes(rows: Sequence) -> tuple[tuple[int, float, float, float, float], ...]:
    """One box per page around the printed rows of a sheet table."""
    pages: dict[int, list] = {}
    for row in rows:
        if row is not None:
            pages.setdefault(row.page_number, []).append(row.rect)
    boxes = []
    for page, rects in sorted(pages.items()):
        x, y = min(r.x for r in rects), min(r.y for r in rects)
        right, bottom = max(r.x + r.w for r in rects), max(r.y + r.h for r in rects)
        boxes.append(
            (page, round(x, 4), round(y, 4), round(min(right, 1) - x, 4), round(min(bottom, 1) - y, 4))
        )
    return tuple(boxes)


def _sheet_title(row: Sequence[str], roles: Mapping[str, int]) -> str:
    """The title row above a header on a sheet ("III | Base Vehicle Floor Plans"): its longest text.

    Text over the price columns ("Unit prices" above "Unit Price in ZAR") heads columns, not the table.
    """
    name = roles.get("description", roles.get("part", len(row)))
    texts = [c for i, c in enumerate(row) if c and any(ch.isalpha() for ch in c) and i <= name]
    others = [c for i, c in enumerate(row) if c.strip() and i > name]
    return max(texts, key=len)[:CELL_CHARS] if 0 < len(texts) <= 2 and not others else ""


# ── reading ─────────────────────────────────────────────────────────────────
def _hits(text: str, patterns: Iterable[tuple[re.Pattern[str], int]]) -> int:
    """Weight of the longest keyword found in ``text`` (0 when none)."""
    return max((weight for pattern, weight in patterns if pattern.search(text)), default=0)


PREFERENCE = ("description", "quantity", "part", "no", "spec", "price", "total", "unit", "months")
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
        if text and len(text) <= HEADER_CHARS:
            pairs += [(_hits(text, compiled[role]), role, index) for role in COLUMN_ROLES]
    pairs.sort(key=lambda p: (-p[0], PREFERENCE.index(p[1]), p[2]))
    found: dict[str, int] = {}
    for score, role, index in pairs:
        if score and role not in found and index not in found.values():
            found[role] = index
    return found


def _readable(columns: Mapping[str, int]) -> bool:
    return "quantity" in columns and ("description" in columns or "part" in columns)


NAME_SCAN = 30
"""Rows under a header looked at to find the column of item names."""


def _header_roles_at(
    rows: Sequence[Sequence[str]], index: int, compiled: Mapping[str, tuple]
) -> dict[str, int] | None:
    """Roles of a header at ``rows[index]``, or None when it is not a price-sheet header.

    A header naming a quantity and a price, whose item-name column has a title the rules do not
    know ("Contract Options", "Base Vehicles"), still is one: the name column is the one whose
    cells below hold words in most rows.
    """
    roles = header_roles(rows[index], compiled)
    if _readable(roles):
        return roles
    if "quantity" not in roles or not {"price", "total"} & set(roles):
        return None
    below = rows[index + 1 : index + 1 + NAME_SCAN]
    width = len(rows[index])
    counts = {
        col: sum(
            1
            for row in below
            if col < len(row) and sum(ch.isalpha() for ch in row[col]) >= 3 and not INTEGER.match(row[col])
        )
        for col in range(width)
        if col not in roles.values()
    }
    best = max(counts, key=lambda col: (counts[col], -col), default=None)
    if best is None or counts[best] < 3:
        return None
    return {**roles, "description": best}


def _check_number(grid: Grid, header: int, columns: dict[str, int]) -> dict[str, int]:
    """An item-number column holds numbers. A column of names under "Items" is the item name.

    "Items to be Supplied | Quantity | Description/Specifications": "Items" matched the number
    words, but its cells are names, so it becomes the description (and a description column
    already found becomes the specification).
    """
    index = columns.get("no")
    if index is None:
        return columns
    cells = [_cell(row, index) for row in grid.rows[header + 1 :]]
    filled = [c for c in cells if c]
    if not filled or sum(1 for c in filled if any(ch.isdigit() for ch in c)) * 2 >= len(filled):
        return columns
    fixed = {role: i for role, i in columns.items() if role != "no"}
    if "description" not in fixed:
        fixed["description"] = index
    elif "spec" not in fixed:
        fixed["spec"], fixed["description"] = fixed["description"], index
    return fixed


def _filled(grid: Grid, start: int, index: int) -> int:
    return sum(1 for row in grid.rows[start:] if _cell(row, index))


def _align(grid: Grid, header: int, columns: dict[str, int]) -> dict[str, int]:
    """Move a role to the next column when the values are printed one cell off their header.

    A table read from a PDF may split a header cell from the cells under it ("Description" in one
    column, the names in the next). A role whose column is empty under the header moves to a
    neighbouring column that has values, no header text and no other role. Price columns are
    left alone: they are blank on a sheet sent out for quotation.
    """
    head = grid.rows[header]
    fixed = dict(columns)
    for role, index in columns.items():
        if role in ("price", "total", "unit") or _filled(grid, header + 1, index):
            continue  # price cells are blank for the supplier to fill
        for near in (index + 1, index - 1):
            free = 0 <= near < len(head) and not head[near].strip() and near not in fixed.values()
            if free and _filled(grid, header + 1, near):
                fixed[role] = near
                break
    return fixed


def _quantity(text: str) -> float | None:
    text = text.strip(" $€£")
    amount = parse_number(text, "en")
    if amount is None and (match := COUNT.match(text)):
        amount = parse_number(match.group(1), "en")
    return amount


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
    head_columns = columns if reading.header is not None else state["head_columns"]
    quantity_head = _cell(header, head_columns.get("quantity"))
    months = columns.get("months")
    monthly = months is not None and bool(_hits(quantity_head, compiled["monthly"]))
    yearly = bool(_hits(quantity_head, compiled["yearly"])) or months is not None
    for index in range(start, len(grid.rows)):
        row = grid.rows[index]
        filled = [c for c in row if c]
        text = normalize(" ".join(filled))
        heading = _group(text, compiled)
        span = grid.spans[index] if grid.spans else None
        if _hits(text, compiled["total_row"]) and not INTEGER.match(_cell(row, columns.get("no")) or "x"):
            continue  # "Category 1 – Brakes Subtotal: $"
        at = next((i for i, c in enumerate(row) if c), 0)
        numeric = {i for role, i in columns.items() if role not in ("description", "spec")}
        note = (
            len(filled) == 1 and not heading and at > 0 and at not in numeric and not INTEGER.match(filled[0])
        )
        if note and ((span is not None and span > 0) or at != columns.get("description")):
            # A note on its own row, under the specification or merged across the row
            # ("Input current: 16A"): more of the item above.
            if state["last"] is not None and state["last_at"] and state["last_at"][0] == grid.id:
                state["last"]["name"] = normalize(f"{state['last']['name']}; {text}")[:200]
                state["last_at"] = (grid.id, index)
            continue
        if heading or (span == 0 and len(filled) == 1 and not INTEGER.match(filled[0])):
            # A group title repeated with the header on the next page of the same sheet is
            # the same run; bump only when the group name actually changes (a revision).
            new_group = heading or text[:80]
            if new_group != state["group"]:
                state["run"] += 1
            state["group"] = new_group
            continue
        amount = _quantity(_cell(row, columns["quantity"]))
        if monthly and amount is not None:
            count = parse_number(_cell(row, months), "en")
            amount = amount * count if count is not None else None
        name = _cell(row, columns.get("description"))
        spec = _cell(row, columns.get("spec"))
        part = _cell(row, columns.get("part")) or None
        number = None
        if grid.sheet and name and INTEGER.match(name) and "description" in columns:
            # "Item | (blank) | Quantity": the option number sits under the header word and
            # its name in the unheaded cell beside it ("1 | Double Passenger Seat").
            beside = columns["description"] + 1
            text_beside = _cell(row, beside)
            if beside not in columns.values() and any(c.isalpha() for c in text_beside):
                number, name = name, text_beside
        if reading.open:
            amount = amount or None  # "0" in a quantity cell left for the buyer
        if (
            (reading.open or grid.sheet)
            and amount is None
            and number is None  # a numbered option with no price ("5 | Power Door Kit INCLUDED")
            and not any(
                _quantity(_cell(row, columns[role])) is not None
                for role in ("price", "total")
                if role in columns
            )
        ):
            # No quantity and no price: a title over the items, or an empty row. On a sheet the
            # title left of the name column is the category, kept until the next one ("Cyber
            # Security"); the one in the name column is the kind under it ("ISA-3000 Firewall").
            if filled and not INTEGER.match(filled[0]) and any(c.isalpha() for c in text):
                name_at = columns.get("description", columns.get("part", 0))
                outer = normalize(" ".join(c for c in row[:name_at] if c))
                inner = normalize(" ".join(c for c in row[name_at:] if c))
                if outer:
                    state["outer"] = outer
                titles = (
                    outer or state.get("outer"),
                    inner if inner != (outer or state.get("outer")) else "",
                )
                new_group = " · ".join(t for t in titles if t)[:80]
                if new_group != state["group"]:
                    state["run"] += 1
                state["group"] = new_group
            if reading.open or grid.sheet:
                continue
        if (
            not reading.open
            and not grid.sheet  # a spreadsheet cell holds the whole name
            and amount is None
            and name
            and state["last_at"] == (grid.id, index - 1)
            and not _cell(row, columns.get("no"))
        ):
            # A description that wrapped onto the next row of a table read by text alignment.
            state["last"]["name"] = normalize(f"{state['last']['name']} {name}")[:200]
            state["last_at"] = (grid.id, index)
            continue
        if (amount is None and not reading.open) or (
            amount is not None and (amount <= 0 or amount != int(amount))
        ):
            continue
        if part is None and name:
            first, _, rest = name.partition(" ")
            if PART.match(first) and rest:
                part, name = first, rest
        name = normalize(name or part or "")
        if len(name) < 2 or not any(c.isalpha() for c in name):
            continue
        if spec and spec.lower() != name.lower():
            name = normalize(f"{name} – {spec}")
        state["count"] += 1
        line = {
            "no": _cell(row, columns.get("no")) or number or str(state["count"]),
            "part": part,
            "name": name[:200],
            "quantity": int(amount) if amount is not None else None,
            "yearly": yearly,
            "group": state["group"],
            "source_doc": grid.source_doc,
            "page": grid.pages[index],
            "table": grid.id,
            # A workbook is read whole, so a group title seen again is not a revision of the sheet.
            "run": None if grid.sheet else (grid.source_doc, state["run"]),
            # A sheet row is its own item: a counted number ("1" for the base vehicle) may equal
            # an option number printed below it.
            "at": (grid.id, index) if grid.sheet else None,
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
        "head_columns": None,
        "width": None,
        "xs": (),
        "page": 0,
        "open": False,
    }
    for grid in grids:
        reading = Reading(grid)
        # A header the user assigned wins over the rules, and may sit lower than a rules header.
        for index, row in enumerate(grid.rows[:LAYOUT_SCAN]):
            if (layout := layouts.get(signature(row))) is not None:
                reading.header, reading.columns, reading.how = index, dict(layout), "layout"
                break
        else:
            for index in range(min(HEADER_SCAN, len(grid.rows))):
                if (roles := _header_roles_at(grid.rows, index, compiled)) is not None:
                    reading.header, reading.columns, reading.how = index, roles, "rules"
                    break
        if reading.header is not None:
            if reading.how == "rules":
                aligned = reading.columns if grid.sheet else _align(grid, reading.header, reading.columns)
                reading.columns = _check_number(grid, reading.header, aligned)
            around = grid.rows[max(0, reading.header - 3) : reading.header + 2]
            reading.priced = any(
                _hits(normalize(c), compiled[role])
                for row in around
                for c in row
                for role in ("price", "total")
            )
            reading.open = _open_quantity(grid, reading.header, reading.columns)
            head = grid.rows[reading.header]
            state.update(
                header=head,
                columns=reading.columns,
                head_columns=reading.columns,
                width=len(head),
                xs=grid.xs,
                page=grid.pages[-1],
                open=reading.open,
                last=None,
            )
            if grid.part or grid.sheet:
                # The titles of the table above on the sheet (or of another sheet) are not this one's.
                state["group"] = state["outer"] = None
            if grid.sheet and (sheet := grid.id.split(":r")[0]) != state.get("sheet"):
                state["sheet"], state["count"] = sheet, 0  # counted numbers start again per sheet
            if reading.header > 0 and (heading := _group(normalize(" ".join(grid.rows[0])), compiled)):
                if heading != state["group"]:
                    state["run"] += 1
                state["group"] = heading
            _read_rows(reading, reading.header + 1, state, compiled)
        elif state["columns"] is not None and (going_on := _going_on(grid, state)) is not None:
            # The sheet goes on over a page break without repeating its header.
            reading.columns, reading.how, reading.open = going_on, "continued", state["open"]
            _read_rows(reading, 0, state, compiled)
            state.update(
                columns=going_on, width=len(grid.rows[0]), xs=grid.xs or state["xs"], page=grid.pages[-1]
            )
        else:
            state.update(header=None, columns=None, last=None)
        readings.append(reading)
    return readings


def _open_quantity(grid: Grid, header: int, columns: Mapping[str, int]) -> bool:
    """An order form: a price sheet whose quantity column holds no quantity in any row.

    The buyer writes how many they order, so every priced row is an item without a quantity,
    left for the user to fill in (no number is made up).
    """
    if "quantity" not in columns or not {"price", "total"} & set(columns):
        return False
    return not any(
        (amount := _quantity(_cell(row, columns["quantity"]))) is not None and amount > 0
        for row in grid.rows[header + 1 :]
    )


def _going_on(grid: Grid, state: Mapping) -> dict[str, int] | None:
    """The columns of a headerless table that goes on with the sheet above, or None.

    The same number of cells keeps the columns. On the next page a PDF may cut the table into
    other cells (a column split in two, a sliver column more): the columns are then matched by
    where they are printed, when the previous page's table gave its column edges.
    """
    if len(grid.rows[0]) == state["width"]:
        return dict(state["columns"])
    if grid.xs and state.get("xs") and grid.pages[0] - state["page"] <= 1:
        return _matched_columns(state["columns"], state["xs"], grid.xs)
    return None


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
    words: Mapping[str, Sequence[str]] | None = None,
    rendered: Mapping[str, FileLayout] | None = None,
) -> list[tuple[Grid, ...]]:
    """Tables of every file, in file order. ``rendered``: spreadsheet layouts, for the boxes."""
    compiled = rules.compiled({role: tuple(ws) for role, ws in (words or {}).items()})
    found = []
    for file in manifest.files:
        if file.kind == "xlsx" and file.id in parsed:
            found.append(
                table_grids(file.id, parsed[file.id].tables, compiled, (rendered or {}).get(file.id))
            )
        elif file.id in canonical:
            found.append(pdf_grids(file.id, canonical[file.id], rules, words))
    return found


def _lists(readings: Sequence[Reading]) -> list[dict]:
    """Readings that make one sheet: a header and the pages it goes on over.

    A new list starts at a header whose columns hold other things, in another file, or more
    than a page after the last one. A header repeated on every page of the same sheet stays
    one list, even when its wording changes ("Part Number", "MFG. Part Number").
    """
    lists: list[dict] = []
    current: dict | None = None
    for reading in readings:
        grid = reading.grid
        if reading.how == "none":
            current = None
            continue
        if reading.how == "continued" and not reading.lines:
            continue  # text under the sheet on the next page, read by its columns: nothing to add
        head = (
            grid.rows[reading.header]
            if reading.header is not None
            else (current["header"] if current else ())
        )
        joined = (
            current is not None
            and current["source_doc"] == grid.source_doc
            and grid.pages[0] - current["pages"][-1] <= 1
            and not grid.part
            and (
                reading.how == "continued"
                or (len(head) == len(current["header"]) and reading.columns == current["columns"])
            )
        )
        if not joined:
            current = {
                "id": grid.id,
                "source_doc": grid.source_doc,
                "caption": grid.caption or grid.title,
                "header": tuple(head),
                "columns": dict(reading.columns),
                "how": reading.how,
                "priced": False,
                "pages": [],
                "boxes": [],
                "tables": [],
            }
            lists.append(current)
        for page in grid.pages:
            if page not in current["pages"]:
                current["pages"].append(page)
        current["boxes"].extend(grid.boxes)
        current["priced"] = current["priced"] or reading.priced
        current["tables"].append(grid.id)
        for line in reading.lines:
            line["list"] = current["id"]
    return lists


def _words(name: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[^\W\d_]{3,}", name.lower()))


def _same_item(a: dict, b: dict) -> bool:
    """The same thing listed twice: the same quantity and most words of the shorter name."""
    if a["quantity"] != b["quantity"]:
        return False
    wa, wb = _words(a["name"]), _words(b["name"])
    if a["quantity"] is None:
        # No quantity to agree on (an order form): only the same words make the same item,
        # or "14+2 passenger with lift" would match "Lift – Type I, 800#".
        return bool(wa) and wa == wb
    return bool(wa and wb) and len(wa & wb) * 2 >= min(len(wa), len(wb))


REVISED = re.compile(
    r"\b(?:revised|revision|amended|amendment|addendum|updated|überarbeitet|geändert|korrigiert)\b"
    r"|개정|수정본|정정",
    re.IGNORECASE,
)
"""A caption that marks a sheet reprinted by an addendum ("FORM 3.02 PRICE SHEET *REVISED*")."""
REVISION_SHARE = 0.8
"""Share of an earlier list's items a revised list must repeat to replace it."""


def _revisions(lists: Sequence[dict], items: Sequence[dict]) -> dict[str, str]:
    """Earlier list -> the revised list that replaces it.

    An addendum reprints a sheet under a caption marked "revised" and asks for bids on that form,
    sometimes with a column added ("Updated Part Number"). A list it repeats almost whole
    (``REVISION_SHARE`` of its items) is replaced, whatever its columns. A specification and its
    bid form repeat each other too, but neither is captioned as a revision: both stay.
    """
    by_list: dict[str, list[dict]] = {}
    for item in items:
        by_list.setdefault(item["list"], []).append(item)
    replaced: dict[str, str] = {}
    for at, later in enumerate(lists):
        theirs = by_list.get(later["id"], [])
        if not theirs or not REVISED.search(later["caption"] or ""):
            continue
        for earlier in lists[:at]:
            mine = by_list.get(earlier["id"], [])
            if not mine or earlier["id"] in replaced:
                continue
            repeated = sum(1 for x in mine if any(_same_item(x, y) for y in theirs))
            if repeated >= REVISION_SHARE * len(mine):
                replaced[earlier["id"]] = later["id"]
    return replaced


def _overlaps(lists: Sequence[dict], items: Sequence[dict]) -> dict[str, list[dict]]:
    """Per list: the other lists that repeat at least half of its items (a spec sheet and its bid form)."""
    by_list: dict[str, list[dict]] = {}
    for item in items:
        by_list.setdefault(item["list"], []).append(item)
    found: dict[str, list[dict]] = {lst["id"]: [] for lst in lists}
    for a in lists:
        mine = by_list.get(a["id"], [])
        for b in lists:
            theirs = by_list.get(b["id"], [])
            if a is b or not mine or not theirs:
                continue
            count = sum(1 for x in mine if any(_same_item(x, y) for y in theirs))
            if count * 2 >= min(len(mine), len(theirs)):
                found[a["id"]].append({"list": b["id"], "items": count})
    return found


def rfq_lines(
    manifest: PackageManifest,
    parsed: Mapping[str, ParsedFile],
    canonical: Mapping[str, Path],
    rules: PriceSheetRules,
    *,
    words: Mapping[str, Sequence[str]] | None = None,
    layouts: Mapping[str, Mapping[str, int]] | None = None,
    rendered: Mapping[str, FileLayout] | None = None,
) -> dict:
    """The ``lines`` object of the pre-cost setup: price-sheet rows of every file, and the tables seen.

    Rows come grouped in lists (one sheet each, over its pages). An RFQ may print the same items
    twice, as a specification and again as the bid form; the user picks the lists to price, and the
    lists with price columns are suggested. ``words`` are header words a user approved per role;
    ``layouts`` maps a header signature to the columns a user assigned.
    """
    compiled = rules.compiled({role: tuple(ws) for role, ws in (words or {}).items()})
    readings: list[Reading] = []
    for grids in file_grids(manifest, parsed, canonical, rules, words, rendered):
        readings.extend(read_grids(grids, compiled, layouts or {}))
    lists = _lists(readings)[:MAX_LISTS]
    shown = {lst["id"]: lst for lst in lists if lst["id"]}
    rows = [line for reading in readings for line in reading.lines if line.get("list") in shown]
    # A revised sheet lists a group again: keep the last run. Scope by list so a specification
    # and its bid form that share a group name (e.g. "Lot 1") do not erase each other. Across
    # lists, only the same form (identical columns) is treated as a revision / addendum.
    last_in_list = {(row["list"], row["group"]): row for row in rows if row["group"] and row["run"]}
    last_form: dict[tuple, dict] = {}
    for row in rows:
        if not row["group"] or not row["run"]:
            continue
        form = (row["group"], tuple(sorted(shown[row["list"]]["columns"].items())))
        last_form[form] = row
    replaced: dict[str, str] = {}
    latest: dict[tuple, dict] = {}
    for row in rows:
        if row["group"] and row["run"]:
            if row["run"] != last_in_list[(row["list"], row["group"])]["run"]:
                continue
            form = (row["group"], tuple(sorted(shown[row["list"]]["columns"].items())))
            end = last_form[form]
            if end["list"] != row["list"]:
                replaced[row["list"]] = end["list"]
                continue
            key = (row["list"], row["group"], row["no"])
        else:
            key = row["at"] or (row["source_doc"], row["table"], row["no"], row["part"])
        latest.pop(key, None)
        latest[key] = {k: v for k, v in row.items() if k not in ("run", "at")}
    found = list(latest.values())
    revised = _revisions(lists, found)
    replaced.update(revised)
    found = [item for item in found if item["list"] not in revised]
    overlaps = _overlaps(lists, found)
    counts: dict[str, int] = {}
    for item in found:
        counts[item["list"]] = counts.get(item["list"], 0) + 1
    priced = {lst["id"] for lst in lists if lst["priced"] or {"price", "total"} & set(lst["columns"])}
    suggested = {
        lst["id"]
        for lst in lists
        if counts.get(lst["id"])
        and (lst["id"] in priced or not priced or not any(o["list"] in priced for o in overlaps[lst["id"]]))
    }
    # Prefer suggested (usually the bid form) so a long specification ahead of it cannot
    # consume the whole MAX_ITEMS budget and leave the priced list with zero lines.
    preferred = [item for item in found if item["list"] in suggested]
    rest = [item for item in found if item["list"] not in suggested]
    items = (preferred + rest)[:MAX_ITEMS]
    tables = [r for r in readings if r.lines or _looks_like_table(r.grid)]
    return {
        "items": items,
        "total": len(found),
        "limit": MAX_LINES,
        "tables": [_summary(r) for r in tables[:MAX_TABLES]],
        "lists": [
            {
                "id": lst["id"],
                "source_doc": lst["source_doc"],
                "caption": lst["caption"][:120],
                "pages": lst["pages"],
                "header": [c[:CELL_CHARS] for c in lst["header"]],
                "columns": lst["columns"],
                "how": lst["how"],
                "lines": counts.get(lst["id"], 0),
                "has_price": lst["id"] in priced,
                "suggested": lst["id"] in suggested,
                "replaced_by": replaced.get(lst["id"]) if not counts.get(lst["id"]) else None,
                "boxes": [
                    {"page": b[0], "x": b[1], "y": b[2], "w": b[3], "h": b[4]} for b in lst["boxes"][:60]
                ],
                "overlaps": overlaps[lst["id"]],
            }
            for lst in lists
        ],
    }


def find_grid(
    manifest: PackageManifest,
    parsed: Mapping[str, ParsedFile],
    canonical: Mapping[str, Path],
    rules: PriceSheetRules,
    grid_id: str,
    words: Mapping[str, Sequence[str]] | None = None,
) -> Grid | None:
    """Locate one table by id. ``words`` must match the approved set used when listing tables,
    or an unruled PDF table found via approved headers would 404 on re-assignment."""
    return next(
        (
            g
            for grids in file_grids(manifest, parsed, canonical, rules, words)
            for g in grids
            if g.id == grid_id
        ),
        None,
    )


__all__ = ["MAX_ITEMS", "MAX_LINES", "Grid", "find_grid", "header_roles", "rfq_lines", "signature"]
