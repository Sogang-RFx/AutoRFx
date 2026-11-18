"""Spreadsheet to canonical PDF, with the position of every row and cell.

A workbook has no pages, so the canonical PDF is our own rendering: one table
per sheet, laid out by PyMuPDF Story. The layout file written next to it keeps
the cell text exactly as read from the workbook and the rectangle each row and
cell occupies, so a citation can point at ``<sheet>!<cell>`` (CITE-03).

Cells the supplier is asked to fill in are marked ``is_input`` and are never
read as values of the RFQ (COST-05):

* an empty cell with a solid fill colour, the usual marking of a form field;
* a numeric zero in a column whose header names a price, cost, amount or rate.
"""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pymupdf
from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils import get_column_letter

from autorfx.models import FileLayout, Rect, SheetCell, SheetRow

RENDERER = "autorfx-sheet-renderer 1"
PAGE = (842.0, 595.0)  # A4 landscape
MARGIN = 36.0
CSS = (
    "body{font-family:sans-serif;font-size:7.5pt}h2{font-size:10pt;margin:0 0 4pt 0}"
    "table{border-collapse:collapse;margin:0 0 10pt 0}td{border:0.5pt solid #999;padding:1.5pt 3pt}"
    "td.in{background-color:#f2f2f2}"
)
PRICE_HEADER = re.compile(r"price|cost|amount|total|rate|preis|kosten|betrag", re.IGNORECASE)
NO_FILL = {"00000000", "FFFFFFFF", "00FFFFFF"}


@dataclass(frozen=True)
class _Cell:
    column: str
    address: str
    text: str
    is_input: bool


@dataclass(frozen=True)
class _Sheet:
    name: str
    rows: tuple[tuple[int, tuple[_Cell, ...]], ...]
    columns: tuple[str, ...]


def cell_text(value: object) -> str:
    """Text of a cell value, without inventing precision: 2.0 is shown as 2."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _has_fill(cell: Cell) -> bool:
    fill = cell.fill
    if fill is None or fill.fill_type != "solid":
        return False
    rgb = getattr(fill.fgColor, "rgb", None)
    return isinstance(rgb, str) and rgb.upper() not in NO_FILL


def _read(path: Path) -> list[_Sheet]:
    values = load_workbook(str(path), data_only=True)
    formulas = load_workbook(str(path), data_only=False)
    sheets: list[_Sheet] = []
    for sheet in formulas.worksheets:
        cached = values[sheet.title]
        headers: dict[int, str] = {}
        rows: list[tuple[int, tuple[_Cell, ...]]] = []
        used: set[int] = set()
        for row in sheet.iter_rows():
            cells: list[_Cell] = []
            for cell in row:
                shown = cached.cell(row=cell.row, column=cell.column).value
                text = cell_text(shown if shown is not None else cell.value)
                header = headers.get(cell.column, "")
                is_zero = isinstance(shown, int | float) and not isinstance(shown, bool) and shown == 0
                is_input = (not text and _has_fill(cell)) or (is_zero and bool(PRICE_HEADER.search(header)))
                if text and not isinstance(shown, int | float):
                    headers[cell.column] = text
                if not text and not is_input:
                    continue
                letter = get_column_letter(cell.column)
                cells.append(_Cell(letter, f"{letter}{cell.row}", text, is_input))
                used.add(cell.column)
            if cells:
                rows.append((row[0].row, tuple(cells)))
        if rows:
            sheets.append(_Sheet(sheet.title, tuple(rows), tuple(get_column_letter(c) for c in sorted(used))))
    return sheets


def _html(sheets: list[_Sheet]) -> str:
    parts: list[str] = []
    for index, sheet in enumerate(sheets):
        parts.append(f'<h2 id="s|{index}">{html.escape(sheet.name)}</h2><table>')
        for row_index, cells in sheet.rows:
            by_column = {c.column: c for c in cells}
            tds = []
            for column in sheet.columns:
                cell = by_column.get(column)
                if cell is None:
                    tds.append("<td></td>")
                    continue
                klass = ' class="in"' if cell.is_input else ""
                body = html.escape(cell.text) if cell.text else "&#160;"
                tds.append(f'<td id="c|{index}|{cell.address}"{klass}>{body}</td>')
            parts.append(f'<tr id="r|{index}|{row_index}">{"".join(tds)}</tr>')
        parts.append("</table>")
    return "".join(parts)


def _norm(rect: tuple[float, float, float, float]) -> Rect:
    x0, y0, x1, y1 = rect
    clamp = lambda v: min(max(v, 0.0), 1.0)  # noqa: E731
    x, y = clamp(x0 / PAGE[0]), clamp(y0 / PAGE[1])
    return Rect(x=x, y=y, w=clamp((x1 - x0) / PAGE[0]), h=clamp((y1 - y0) / PAGE[1]))


def render_xlsx(source: Path, target: Path, file_id: str) -> tuple[FileLayout, str]:
    """Write the canonical PDF of a workbook and return the layout of its rows and cells."""
    from autorfx.ingest.convert import ConversionError, save_deterministic

    try:
        sheets = _read(source)
    except Exception as exc:  # openpyxl raises zipfile, KeyError or XML errors for bad input
        raise ConversionError("XLSX를 열 수 없습니다. 파일이 손상됐을 수 있습니다.") from exc
    if not sheets:
        raise ConversionError("XLSX에 내용이 있는 시트가 없습니다.")

    positions: dict[str, tuple[int, tuple[float, float, float, float]]] = {}
    buffer = io.BytesIO()
    story = pymupdf.Story(html=_html(sheets), user_css=CSS)
    writer = pymupdf.DocumentWriter(buffer)
    mediabox = pymupdf.Rect(0, 0, *PAGE)
    where = mediabox + (MARGIN, MARGIN, -MARGIN, -MARGIN)

    def record(position: pymupdf.ElementPosition) -> None:
        """Keep the first place of every element that carries an id (a row may continue on a new page)."""
        key = position.id
        if key and position.open_close & 1 and key not in positions:
            positions[key] = (position.page, tuple(position.rect))

    page_number, more = 0, True
    while more:
        page_number += 1
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.element_positions(record, {"page": page_number})
        story.draw(device)
        writer.end_page()
    writer.close()
    with pymupdf.open(stream=buffer.getvalue(), filetype="pdf") as doc:
        save_deterministic(doc, target)

    rows: list[SheetRow] = []
    for index, sheet in enumerate(sheets):
        for row_index, cells in sheet.rows:
            placed = positions.get(f"r|{index}|{row_index}")
            if placed is None:
                raise ConversionError(f"시트 '{sheet.name}'의 {row_index}행 위치를 기록하지 못했습니다.")
            page, rect = placed
            row_rect = _norm(rect)
            rows.append(
                SheetRow(
                    sheet=sheet.name,
                    row_index=row_index,
                    page_number=page,
                    rect=row_rect,
                    cells=tuple(
                        SheetCell(
                            anchor=f"{sheet.name}!{cell.address}",
                            column=cell.column,
                            text=cell.text,
                            rect=_norm(positions[f"c|{index}|{cell.address}"][1])
                            if f"c|{index}|{cell.address}" in positions
                            else row_rect,
                            is_input=cell.is_input,
                        )
                        for cell in cells
                    ),
                )
            )
    return FileLayout(file_id=file_id, rows=tuple(rows)), f"{RENDERER} + pymupdf-story {pymupdf.__version__}"
