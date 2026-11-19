"""Value normalisation (CONSIST-02).

Two values are compared only after both are in one canonical form, because
the same value is written differently across the files of a package:

* numbers: German "1.234,56" and "150.000" (thousands), English "1,234.56";
* unit multipliers in headers and labels: "[k pcs]", "Tsd.", "Mio.";
* dates: "04.12.2026" (day first), "12/04/2026" (ambiguous: both readings are
  kept), "06/2028", "März 2028", "KW 10/2028" (the month of that ISO week).

Dates are canonical as ``YYYY-MM-DD`` or ``YYYY-MM``. An ambiguous date keeps
its candidates joined by ``|``; it conflicts with another date only when no
candidate agrees.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

NUMBER = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
MULTIPLIERS = {"k": 1000, "tsd": 1000, "thousand": 1000, "mio": 1_000_000, "million": 1_000_000}
MULTIPLIER = re.compile(r"(?<![A-Za-z])(k|tsd|thousand|mio|million)\b\.?", re.IGNORECASE)
MONTHS = {
    name: number
    for number, names in enumerate(
        (
            "januar january jan",
            "februar february feb",
            "märz maerz march mar",
            "april apr",
            "mai may",
            "juni june jun",
            "juli july jul",
            "august aug",
            "september sept sep",
            "oktober october okt oct",
            "november nov",
            "dezember december dez dec",
        ),
        start=1,
    )
    for name in names.split()
}
ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DOTTED = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
SLASHED = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
WEEK = re.compile(r"\b(?:KW|CW|week)\s*(\d{1,2})\s*/\s*(\d{4})\b", re.IGNORECASE)
MONTH_YEAR = re.compile(r"(?<![\d/])(\d{1,2})/(\d{4})\b")
MONTH_NAME = re.compile(r"\b([A-Za-zÄäÖöÜü]{3,9})\.?\s+(\d{4})\b")


def parse_number(text: str, lang: str) -> Decimal | None:
    """A number written in the conventions of ``lang`` ("de" or anything else for English)."""
    token = re.sub(r"[\s ]", "", text)
    if not re.fullmatch(r"[\d.,]+", token) or not any(ch.isdigit() for ch in token):
        return None
    dot, comma = "." in token, "," in token
    if dot and comma:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
    elif comma:
        decimal = None if lang != "de" and re.fullmatch(r"\d{1,3}(,\d{3})+", token) else ","
    elif dot:
        decimal = None if lang == "de" and re.fullmatch(r"\d{1,3}(\.\d{3})+", token) else "."
    else:
        decimal = None
    if decimal is None:
        digits = re.sub(r"[.,]", "", token)
    else:
        digits = token.replace("," if decimal == "." else ".", "").replace(decimal, ".")
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def plain(value: Decimal) -> str:
    """Decimal as a plain string: no exponent, no trailing zeros ("2.50" and "2.5" are equal)."""
    text = format(value.normalize(), "f")
    return text if text != "-0" else "0"


def multiplier_of(label: str) -> Decimal:
    """The factor a header or unit label implies: "[k pcs]" is 1000."""
    match = MULTIPLIER.search(label)
    return Decimal(MULTIPLIERS[match.group(1).casefold()]) if match else Decimal(1)


def _date(year: int, month: int, day: int) -> str | None:
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_dates(text: str, lang: str) -> tuple[str, ...]:
    """Canonical candidates of the first date in ``text``; empty when there is none."""
    if match := ISO.search(text):
        found = _date(int(match[1]), int(match[2]), int(match[3]))
        return (found,) if found else ()
    if match := DOTTED.search(text):
        found = _date(int(match[3]), int(match[2]), int(match[1]))
        return (found,) if found else ()
    if match := SLASHED.search(text):
        first, second, year = int(match[1]), int(match[2]), int(match[3])
        readings = [_date(year, first, second), _date(year, second, first)]
        if lang == "de":
            readings.reverse()
        return tuple(dict.fromkeys(r for r in readings if r))
    if match := WEEK.search(text):
        try:
            monday = dt.date.fromisocalendar(int(match[2]), int(match[1]), 1)
        except ValueError:
            return ()
        return (f"{monday.year:04d}-{monday.month:02d}",)
    if (match := MONTH_YEAR.search(text)) and 1 <= int(match[1]) <= 12:
        return (f"{int(match[2]):04d}-{int(match[1]):02d}",)
    for match in MONTH_NAME.finditer(text):
        month = MONTHS.get(match[1].casefold())
        if month:
            return (f"{int(match[2]):04d}-{month:02d}",)
    return ()


def dates_agree(left: str, right: str) -> bool:
    """True when any candidate of ``left`` equals any of ``right`` at their coarser precision."""
    for a in left.split("|"):
        for b in right.split("|"):
            size = min(len(a), len(b))
            if a[:size] == b[:size]:
                return True
    return False
