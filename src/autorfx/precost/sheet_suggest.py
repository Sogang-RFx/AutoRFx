"""An AI suggestion for the columns of a table the rules could not read.

The model sees the first rows of the table, cell by cell, and answers with positions only: the
header row and the column of each role (:class:`~autorfx.analysis.SheetMap`). It never writes an
item, a name or a number. Code checks the answer against the cells and reads the table with it
once, so a suggestion that reads no item is refused. The user sees the suggestion in the column
assigner and accepts it there; only then is it remembered, like a layout assigned by hand.

Only the sample rows are sent, and only when the user asks (the button in the assigner).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from autorfx.analysis import AnalysisProvider, TransientProviderError
from autorfx.precost.lines import CELL_CHARS, LAYOUT_SCAN, Grid, read_grids, signature
from autorfx.precost.sheet_store import PriceSheetError, check_columns
from autorfx.rules import COLUMN_ROLES

SAMPLE_CHARS = 60
"""A cell is cut to this many characters in the sample: enough to name a column."""
PREVIEW = 5


class SuggestError(ValueError):
    """No usable suggestion; the message says why, in Korean, for the screen."""


def sheet_sample(grid: Grid, rows: int = LAYOUT_SCAN) -> str:
    """The first rows of a table as the model reads them: ``R3: C0=No. | C2=Description``."""
    lines = []
    for index, row in enumerate(grid.rows[:rows]):
        cells = " | ".join(
            f"C{col}={' '.join(cell.split())[:SAMPLE_CHARS]}" for col, cell in enumerate(row) if cell
        )
        lines.append(f"R{index}: {cells}" if cells else f"R{index}:")
    return "\n".join(lines)


def suggest_columns(grid: Grid, provider: AnalysisProvider, compiled: Mapping[str, tuple]) -> dict:
    """The model's map of ``grid``, checked by reading the table with it.

    Returns ``header_row``, ``columns``, ``reason``, the number of ``lines`` read, the first
    item names (``preview``), the ``rows`` to show (down to a few rows under the header) and the
    ``engine``. Raises :class:`SuggestError` when there is no model or the answer does not read.
    """
    if not provider.uses_llm:
        raise SuggestError("LLM이 연결되어 있지 않아 AI 제안을 쓸 수 없습니다. 열을 직접 지정하세요.")
    try:
        found = provider.map_sheet(sheet_sample(grid))
    except TransientProviderError as exc:
        raise SuggestError(
            "AI가 지금 응답하지 않습니다. 잠시 뒤 다시 시도하거나 열을 직접 지정하세요."
        ) from exc
    if found is None:
        raise SuggestError("AI 제안을 받지 못했습니다. 열을 직접 지정하세요.")
    if not found.is_price_sheet:
        raise SuggestError(f"AI는 이 표를 품목표가 아니라고 봤습니다: {found.reason}")
    header = found.header_row
    sampled = min(len(grid.rows), LAYOUT_SCAN)
    if header is None or not 0 <= header < sampled:
        raise SuggestError("AI가 고른 머리글 줄이 표 밖입니다. 열을 직접 지정하세요.")
    header_cells = grid.rows[header]
    if sum(1 for c in header_cells if c and str(c).strip()) < 2:
        raise SuggestError(
            "AI가 고른 머리글 줄에 열이 너무 적습니다. 빈 줄이나 제목 줄이 아닙니다. 열을 직접 지정하세요."
        )
    width = max((len(row) for row in grid.rows), default=0)
    proposed = {role: getattr(found, role) for role in COLUMN_ROLES if getattr(found, role) is not None}
    try:
        columns = check_columns(proposed, width)
    except PriceSheetError as exc:
        raise SuggestError(f"AI 제안을 쓸 수 없습니다: {exc}") from exc
    # Read the table once with the suggestion, from its header down, as the assignment would.
    below = dataclasses.replace(
        grid,
        rows=grid.rows[header:],
        pages=grid.pages[header:],
        spans=grid.spans[header:] if grid.spans else (),
        part=False,
    )
    (reading, *_) = read_grids([below], compiled, {signature(header_cells): columns})
    if not reading.lines:
        raise SuggestError(
            "AI가 고른 열로는 품목을 하나도 읽지 못했습니다. "
            "수량 열에 숫자가 있는지 확인하고 열을 직접 지정하세요."
        )
    return {
        "header_row": header,
        "columns": columns,
        "reason": found.reason[:300],
        "lines": len(reading.lines),
        "preview": [line["name"][:CELL_CHARS] for line in reading.lines[:PREVIEW]],
        "rows": [[c[:CELL_CHARS] for c in row] for row in grid.rows[: max(header + 6, 8)]],
        "engine": provider.id,
    }


__all__ = ["SuggestError", "sheet_sample", "suggest_columns"]
