"""Table rows as evidence: which row is the header, and whether a value belongs to its column (INV-3).

A row such as "Vehicles | 60 | 120" says nothing about years; its header "Year | 2028 | 2029" does. Both
figures of "2029년 60" stand in the table, so the number check alone would accept a wrong pair.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby

from autorfx.chat.figures import Figure, figures
from autorfx.chat.models import Evidence
from autorfx.chat.numbers import unsupported_numbers

SEPARATOR = " | "
MIN_CELLS = 3
"""A table row has at least this many cells; "SOP | 06/2028" is a labelled value."""


@dataclass(frozen=True, slots=True)
class Columns:
    """How the header figures and the row figures of one statement go together."""

    paired: int = 0
    mismatch: bool = False


def is_row(evidence: Evidence) -> bool:
    return evidence.text.count(SEPARATOR) >= MIN_CELLS - 1


def header_of(row: Evidence, evidence: Sequence[Evidence]) -> Evidence | None:
    """The first of the table rows that stand directly above a row, without a gap."""
    if not is_row(row):
        return None
    below, first = row, row
    for above in reversed([e for e in evidence if e.number < row.number]):
        if not is_row(above) or not _directly_above(above, below):
            break
        below = first = above
    return first if first.number != row.number else None


def _directly_above(above: Evidence, below: Evidence) -> bool:
    a, b = above.locus, below.locus
    same = (a.source_doc, a.page_number, a.section_id) == (b.source_doc, b.page_number, b.section_id)
    return same and a.sentence_index == b.sentence_index - 1


def columns(text: str, header: Evidence, rows: Sequence[Evidence]) -> Columns:
    """Pair each header figure of the text with the row figure that follows it."""
    head = header.text.split(SEPARATOR)
    cells = [row.text.split(SEPARATOR) for row in rows]
    marked: list[tuple[str, set[int]]] = []
    for figure in figures(text):
        in_head = _where(figure, head)
        in_rows = {i for row in cells for i in _where(figure, row)}
        if in_head and not in_rows:
            marked.append(("head", in_head))
        elif in_rows:
            marked.append(("row", in_rows))
    runs = [(kind, [where for _, where in run]) for kind, run in groupby(marked, key=lambda item: item[0])]
    paired = 0
    for (kind, heads), (_, values) in zip(runs, runs[1:], strict=False):
        # "2028년부터 2033년까지 60, 120, ..." is a span: it names no column for each value.
        if kind != "head" or (len(heads) > 1 and len(heads) != len(values)):
            continue
        for column, value in zip(heads, values, strict=False):
            if not column & value:
                return Columns(mismatch=True)
            paired += 1
    return Columns(paired=paired)


def _where(figure: Figure, cells: Sequence[str]) -> set[int]:
    """The columns that state the figure. The first cell is the label of the row."""
    if figure.identifier:
        return set()
    plain = figure.thousands or figure.text
    written = f"{plain}년" if figure.dates and plain.isdecimal() else plain
    return {i for i, cell in enumerate(cells) if i > 0 and not unsupported_numbers(written, (cell,))}
