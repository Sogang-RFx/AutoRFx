"""Spreadsheet layout to sentences and tables (PARSE-03).

Each non-empty row is one citable unit ("sentence") whose text is the cell
texts joined with " | " and whose anchor names its cells. Each run of
consecutive rows becomes a table, with its first row as the header when that
row holds only text. Cells the supplier must fill in carry no value and are
left out of the row text.
"""

from __future__ import annotations

import re
from collections import Counter

from autorfx.models import FileLayout, Locus, ParsedFile, ParsedSentence, SectionInfo, SheetRow, Table
from autorfx.parser.sentences import detect_language

NUMERIC = re.compile(r"^[\s\d.,%+\-–]*$")


def _column_key(column: str) -> tuple[int, str]:
    return (len(column), column)


def _table(file_id: str, sheet: str, rows: list[tuple[SheetRow, Locus]]) -> Table:
    columns = sorted({c.column for row, _ in rows for c in row.cells}, key=_column_key)
    grid: list[tuple[str, ...]] = []
    anchors: list[tuple[str | None, ...]] = []
    for row, _ in rows:
        by_column = {c.column: c for c in row.cells}
        grid.append(tuple(by_column[c].text if c in by_column else "" for c in columns))
        anchors.append(tuple(by_column[c].anchor if c in by_column else None for c in columns))
    first = [text for text in grid[0] if text]
    has_header = len(rows) > 1 and bool(first) and not any(NUMERIC.match(text) for text in first)
    body = slice(1, None) if has_header else slice(None)
    return Table(
        source_doc=file_id,
        page_number=rows[0][0].page_number,
        section_id=sheet,
        title=sheet,
        header=grid[0] if has_header else (),
        rows=tuple(grid[body]),
        row_loci=tuple(locus for _, locus in rows[body]),
        cell_anchors=tuple(anchors[body]),
    )


def parse_sheets(file_id: str, layout: FileLayout, page_count: int) -> ParsedFile:
    """Rows of a rendered workbook as sentences, one section per sheet, one table per block of rows."""
    sentences: list[ParsedSentence] = []
    sections: list[SectionInfo] = []
    tables: list[Table] = []
    counter: Counter[int] = Counter()
    block: list[tuple[SheetRow, Locus]] = []
    block_sheet: str | None = None

    def flush() -> None:
        if block and block_sheet is not None:
            tables.append(_table(file_id, block_sheet, list(block)))
        block.clear()

    for row in layout.rows:
        cells = [c for c in row.cells if c.text]
        if not cells:
            continue
        if not any(s.section_id == row.sheet for s in sections):
            sections.append(
                SectionInfo(section_id=row.sheet, title=row.sheet, path=(row.sheet,), kind="table")
            )
        if block and (block_sheet != row.sheet or row.row_index != block[-1][0].row_index + 1):
            flush()
        block_sheet = row.sheet
        counter[row.page_number] += 1
        first, last = cells[0].anchor, cells[-1].anchor.rsplit("!", 1)[-1]
        locus = Locus(
            source_doc=file_id,
            page_number=row.page_number,
            section_id=row.sheet,
            paragraph_index=counter[row.page_number],
            sentence_index=counter[row.page_number],
            anchor=first if len(cells) == 1 else f"{first}:{last}",
        )
        sentences.append(
            ParsedSentence(
                locus=locus, text=" | ".join(c.text for c in cells), rects=(row.rect,), role="table"
            )
        )
        block.append((row, locus))
    flush()
    return ParsedFile(
        file_id=file_id,
        lang=detect_language(" ".join(s.text for s in sentences)[:30000]),
        page_count=page_count,
        sentences=tuple(sentences),
        sections=tuple(sections),
        tables=tuple(tables),
    )
