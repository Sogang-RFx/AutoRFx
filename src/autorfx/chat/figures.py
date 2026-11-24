"""Reading the figures of a text: weeks, identifiers, dates, values, numbers (docs/design/chat.md §3.2).

Used for the answer and for the cited sentences alike, so a figure copied as it stands reads the
same on both sides. Patterns are tried from the most to the least specific, and text that one of
them read is not read again: the 4 of "4. März 2028" is a day, not a number that could support "4개".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from autorfx.chat.units import unit_at
from autorfx.chat.weeks import (
    IDENTIFIER,
    KOREAN_WEEK,
    LATIN,
    LATIN_WEEK,
    MONTH_WEEK,
    Week,
    is_identifier,
    korean_weeks,
    latin_weeks,
)

# A space (or a Swiss apostrophe) groups thousands only where nothing else is possible: "200 000".
NUM = (
    r"(?<!\d)(?<!\d[.,])(?:\d{1,3}(?: \d{3}){0,3} 0\d{2}(?!\d)"
    r"|\d+(?:(?:[.,]|['’\N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}\N{THIN SPACE}](?=\d{3}(?!\d)))\d+)*)"
)
GROUPING = re.compile(r"['’\s]")

MONTHS = {
    name: number
    for number, names in enumerate(
        (
            "january januar jan",
            "february februar feb",
            "march märz maerz mar mär mrz",
            "april apr",
            "may mai",
            "june juni jun",
            "july juli jul",
            "august aug",
            "september sept sep",
            "october oktober oct okt",
            "november nov",
            "december dezember dec dez",
        ),
        start=1,
    )
    for name in names.split()
}
# Capitalised only: "may 2028" is a verb far more often than a month.
_NAMES = sorted((n for name in MONTHS for n in (name.capitalize(), name.upper())), key=len, reverse=True)
MONTH = rf"(?<!\w)({'|'.join(_NAMES)})(?!\w)\.?"
ORDINAL = r"(?:st|nd|rd|th)?"

SMALL_UNITS = {"십": 10, "백": 100, "천": 1000}
LARGE_UNITS = {"만": 10**4, "억": 10**8, "조": 10**12}
END = r"(?![A-Za-zÄÖÜäöüß\d])"
MONEY = r"(?=\s?(?:€|EUR|USD))"
# "billion" is 10^9 in English and "Billion" 10^12 in German: capitalised, it may be either.
FACTORS: tuple[tuple[str, tuple[int, ...]], ...] = (
    (rf"(?:(?i:thousand|tausend|tsd\.?)|k){END}|[kT]{MONEY}", (10**3,)),
    (rf"(?i:mio\.?|millionen|millions?){END}|M{MONEY}", (10**6,)),
    (rf"(?:(?i:mrd\.?|milliarden?)|billions?|bn){END}", (10**9,)),
    (rf"(?:Billion(?:s|en)?|BILLIONS?){END}", (10**9, 10**12)),
)

Parts = tuple[int | None, int | None, int | None]


@dataclass(frozen=True, slots=True)
class Figure:
    """One figure of a text. Which fields are set says what kind it is."""

    text: str
    dates: tuple[Parts, ...] = ()
    """Year, month, day; None for a part that is not given. Two for "12월 4일~15일"."""
    written: tuple[str, ...] = ()
    """A numeric date as it is written; as such it may be copied, and only copied."""
    values: frozenset[Decimal] | None = None
    """A multiplied value ("5 Mio."); more than one when the notation is ambiguous."""
    digits: str = ""
    weeks: tuple[Week, ...] = ()
    """Calendar weeks. A week is never a month and never a plain number."""
    identifier: bool = False
    """A part number, standard or grade: one token, compared as it is written."""
    unit: str = ""
    """Of a plain number: a duration or a currency, where one is written."""
    thousands: str = ""
    """The number of a value that is written with k: the "60" of "60 k"."""


def readings(token: str) -> frozenset[Decimal]:
    """Every value a number may have: "200.000" is 200000 in German and 200 in English."""
    core = GROUPING.sub("", token)
    marks = {c for c in core if c in ".,"}
    if not marks:
        return frozenset((Decimal(core),))
    if len(marks) == 2:
        decimal = max(marks, key=core.rfind)
        whole, _, fraction = core.rpartition(decimal)
        grouped = _ungrouped(whole, (marks - {decimal}).pop())
        return frozenset((Decimal(f"{grouped}.{fraction}"),)) if grouped else frozenset()
    mark = marks.pop()
    found = set()
    if core.count(mark) == 1:
        found.add(Decimal(core.replace(mark, ".")))
    if grouped := _ungrouped(core, mark):
        found.add(Decimal(grouped))
    return frozenset(found)


def korean_value(token: str) -> Decimal | None:
    """The value a Korean reader sees: a comma groups thousands, a dot is the decimal point."""
    whole, _, fraction = GROUPING.sub("", token).partition(".")
    digits = _ungrouped(whole, ",") if "," in whole else whole
    if not digits or (fraction and not fraction.isdecimal()):
        return None
    return Decimal(f"{digits}.{fraction}" if fraction else digits)


def _ungrouped(text: str, mark: str) -> str:
    first, *rest = text.split(mark)
    valid = first.isdecimal() and 1 <= len(first) <= 3 and first[0] != "0"
    return first + "".join(rest) if valid and all(len(r) == 3 and r.isdecimal() for r in rest) else ""


# ── dates ───────────────────────────────────────────────────────────────────
def _korean(m: re.Match[str]) -> Figure:
    year, month = int(m["y"]) if m["y"] else None, int(m["m"])
    days = (m["d"], m["d2"]) if m["d2"] else (m["d"],)
    return Figure(m.group(), dates=tuple((year, month, int(d)) for d in days))


def _year_month(m: re.Match[str]) -> Figure:
    return Figure(m.group(), dates=((int(m["y"]), int(m["m"]), None),))


def _year(m: re.Match[str]) -> Figure:
    return Figure(m.group(), dates=((int(m.group()), None, None),))


def _month(m: re.Match[str]) -> Figure:
    """A month alone is read as one, so that "12 Monate" cannot support "12월"."""
    return Figure(m.group(), dates=((None, int(m[1]), None),))


def _month_first(m: re.Match[str]) -> Figure:
    return Figure(m.group(), dates=((int(m[3]), MONTHS[m[1].casefold()], int(m[2])),))


def _day_first(m: re.Match[str]) -> Figure:
    return Figure(m.group(), dates=((int(m[3]), MONTHS[m[2].casefold()], int(m[1]) if m[1] else None),))


def _year_first(m: re.Match[str]) -> Figure:
    return Figure(m.group(), dates=((int(m[1]), int(m[3]), int(m[4])),))


def _copied(m: re.Match[str], separator: str, date: Parts | None) -> Figure:
    written = (separator, *re.findall(r"\d+", m.group()))
    return Figure(m.group(), dates=(date,) if date else (), written=written)


def _dotted(m: re.Match[str]) -> Figure:
    return _copied(m, ".", _date(int(m[3]), int(m[2]), int(m[1])))


def _slashed(m: re.Match[str]) -> Figure:
    a, b, year = int(m[1]), int(m[2]), int(m[3])
    if a <= 12 and b <= 12:  # 12/04/2026 is April or December: only the year is certain
        return _copied(m, "/", (year, None, None))
    return _copied(m, "/", _date(year, min(a, b), max(a, b)) if min(a, b) <= 12 else None)


def _month_year(m: re.Match[str]) -> Figure:
    return _copied(m, "/", _date(int(m[2]), int(m[1]), None))


def _date(year: int, month: int, day: int | None) -> Parts | None:
    valid = 1 <= month <= 12 and (day is None or 1 <= day <= 31)
    return (year, month, day) if valid else None


# ── values and numbers ──────────────────────────────────────────────────────
PART = rf"{NUM}\s?(?:[십백천](?:\s?[만억조])?|[만억조])"
PART_PIECES = re.compile(rf"({NUM})\s?([십백천])?\s?([만억조])?")


def _korean_units(m: re.Match[str]) -> Figure:
    """ "1억 5천만". The parts are summed only when they descend; anything else is not a value."""
    total, last = Decimal(0), None
    for number, small, large in PART_PIECES.findall(m.group()):
        value = korean_value(number)
        unit = SMALL_UNITS.get(small, 1) * LARGE_UNITS.get(large, 1)
        if value is None or (last is not None and value * unit >= last):
            return Figure(m.group(), values=frozenset())
        total, last = total + value * unit, unit
    return Figure(m.group(), values=frozenset((total,)))


def _multiplied(factors: tuple[int, ...]) -> Callable[[re.Match[str]], Figure]:
    def read(m: re.Match[str]) -> Figure:
        values = frozenset(value * factor for value in readings(m[1]) for factor in factors)
        return Figure(m.group(), values=values, thousands=m[1] if m.group().endswith("k") else "")

    return read


def _number(m: re.Match[str]) -> Figure:
    return Figure(m.group(), digits=re.sub(r"\D", "", m.group()))


def _marker(m: re.Match[str]) -> None:
    return None


def _latin_week(m: re.Match[str]) -> Figure:
    return Figure(m.group(), weeks=latin_weeks(m))


def _korean_week(m: re.Match[str]) -> Figure:
    return Figure(m.group(), weeks=korean_weeks(m))


def _month_week(m: re.Match[str]) -> Figure:
    return Figure(m.group(), values=frozenset())


def _period(m: re.Match[str]) -> Figure:
    """ "Q1/2027" is no month: copied only."""
    return Figure(m.group(), written=(m[1], m[2], m[3]))


def _document_number(m: re.Match[str]) -> Figure:
    """ "Nr. 6/2027" may be the sixth document of 2027: copied only."""
    return Figure(m.group(), written=("/", m[1], m[2]))


def _identifier(m: re.Match[str]) -> Figure:
    return Figure(m.group(), identifier=True)


YEAR = r"(?<!\d)(?P<y>\d{4})\s*년(?:도)?(?:\s*의)?[\s,(]*"
KOREAN_DATE = (
    rf"(?:{YEAR})?(?<!\d)(?P<m>\d{{1,2}})\s*월(?:\s*의)?\s*(?P<d>\d{{1,2}})\s*일"
    r"(?:\s*(?:[~∼–—-]|부터)\s*(?P<d2>\d{1,2})\s*일(?!간))?"
)
DAY_FIRST = (
    rf"(?:(?<!\d)(?<!\d[.,/])(\d{{1,2}}){ORDINAL}\.?[\s-]*(?:of\s+)?)?{MONTH}(?:,\s*|[\s-]+)(\d{{4}})(?!\d)"
)
READERS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], Figure | None]], ...] = (
    (MONTH_WEEK, _month_week),
    (KOREAN_WEEK, _korean_week),
    (LATIN_WEEK, _latin_week),
    (re.compile(rf"(?<![{LATIN}])([QH])(\d{{1,2}})/(\d{{4}})(?!\d)"), _period),
    (
        re.compile(r"(?:(?:[Nn][ro]\.?|#|\))[:\s]*|(?<=[A-Za-z]-))(\d{1,2})/(\d{4})(?![\d/])"),
        _document_number,
    ),
    (re.compile(KOREAN_DATE), _korean),
    (re.compile(rf"{YEAR}(?P<m>\d{{1,2}})\s*월"), _year_month),
    (re.compile(rf"{MONTH}\s*(?<!\d)(\d{{1,2}}){ORDINAL}(?:,\s*|\s+)(\d{{4}})(?!\d)"), _month_first),
    (re.compile(DAY_FIRST), _day_first),
    (re.compile(r"(?<![\d.,/])(\d{4})([./-])(\d{1,2})\2(\d{1,2})(?![.,/]?\d)"), _year_first),
    (re.compile(r"(?<!\d)(\d{4})(\.\s)(\d{1,2})\.\s(\d{1,2})(?![.,]?\d)"), _year_first),
    (re.compile(r"(?<!\d)(?<!\d[.,])(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})(?![.,]?\d)"), _dotted),
    (re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])"), _slashed),
    (re.compile(r"(?<![\d/])(\d{1,2})/(\d{4})(?![\d/])"), _month_year),
    (IDENTIFIER, _identifier),
    # Layout, not a figure. After the dates: "4. März 2028" may start a line.
    (re.compile(r"^[ \t]*\d{1,2}[.)](?=[ \t]+\S)", re.MULTILINE), _marker),
    (re.compile(rf"(?<!제)(?<!제\s){PART}(?:\s?{PART})*(?:\s?{NUM})?"), _korean_units),
    *((re.compile(rf"({NUM})[\s-]?(?:{pattern})"), _multiplied(factors)) for pattern, factors in FACTORS),
    (re.compile(r"(?<!\d)\d{4}(?=\s*년)"), _year),
    (re.compile(r"(?<![\d.,])(\d{1,2})\s*월"), _month),
    (re.compile(NUM), _number),
)


def _blank(m: re.Match[str]) -> str:
    return "\0" * len(m.group())


def _blank_identifier(m: re.Match[str]) -> str:
    return _blank(m) if is_identifier(m.group()) else m.group()


def figures(text: str) -> tuple[Figure, ...]:
    """The figures of a text, in order."""
    found: list[tuple[int, Figure]] = []
    original = text
    for pattern, read in READERS:
        for match in pattern.finditer(text):
            if pattern is IDENTIFIER and not is_identifier(match.group()):
                continue
            if (figure := read(match)) is not None:
                if figure.digits:
                    figure = replace(figure, unit=unit_at(original, match.start(), match.end()))
                found.append((match.start(), figure))
        text = pattern.sub(_blank if pattern is not IDENTIFIER else _blank_identifier, text)
    return tuple(figure for _, figure in sorted(found, key=lambda item: item[0]))
