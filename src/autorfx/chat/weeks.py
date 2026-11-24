"""Calendar weeks and identifiers inside a text (INV-3, docs/design/chat.md §3.2).

"KW 6/2027" is the sixth week of 2027. Read as a number it would support "6", read as a month it would
support "2027년 6월"; both are wrong, so a week is recognised before dates and numbers are.
"""

from __future__ import annotations

import re

Week = tuple[int | None, int]
"""Year and calendar week; None for a year that is not given."""

LATIN = "A-Za-zÀ-ÖØ-öø-ÿ"
"""Without Hangul, so that a particle ("E40은") is not part of a token."""

N = r"(?<!\d)\d{1,2}(?!\d)"
YEAR = r"(?:\s*[/.,]\s*|\s+(?:of\s+)?)(?:19|20)\d{2}(?!\d)"
ENDS_WITH_YEAR = r"(?=[\d\s,]*\d{1,2}\s*/\s*\d{4})"
"""A comma lists weeks only when a year closes the list: in "KW 5, 12 Stück" the 12 is a quantity."""
JOIN = rf"\s*(?:[-–—~∼]|und\b|and\b|bis\b|to\b|부터|,{ENDS_WITH_YEAR})\s*"
LIST = rf"{N}(?:{YEAR})?(?:{JOIN}{N}(?:{YEAR})?)*"
ABBREVIATED = rf"(?<![{LATIN}])(?:KW|CW|WK|[Ww]k\.?)\)?\s*(?:[Nn]r\.?|[Nn]o\.?)?\s*[:|]?\s*"
# The plain word is also a duration ("Lieferzeit in Wochen: 12"): only a week with its year is one.
WORD = rf"(?<!\d\s)(?<![{LATIN}])(?:(?i:calendar\s+)?(?i:weeks?)|[{LATIN}]*(?:[Ww]ochen?))\s+"
WITH_YEAR = r"(?=[\d\s,–—-]*\d{1,2}\s*(?:/|of)\s*\d{4})"
KOREAN_YEAR = r"(?:(?<!\d)(?P<y>\d{4})\s*년(?:도)?\s*)"

LATIN_WEEK = re.compile(
    rf"{KOREAN_YEAR}?(?:{ABBREVIATED}(?P<short>{LIST})|{WORD}{WITH_YEAR}(?P<long>{LIST}))"
    rf"|(?<![\d.,])(?P<n>\d{{1,2}})\.\s*(?:KW|CW)\b(?:\s*/?\s*(?P<ny>\d{{4}})(?!\d))?"
    rf"|(?<!\d)(?P<iy>\d{{4}})-?W(?P<iw>\d{{2}})(?!\d)"
    rf"|(?<![{LATIN}\d])W(?P<w>\d{{2}})(?:[/.-](?P<wy>\d{{4}}))?(?!\d)"
)
MONTH_WEEK = re.compile(r"(?:(?<!\d)\d{4}\s*년\s*)?(?<!\d)\d{1,2}\s*월\s*(?:제\s*)?\d{1,2}\s*주차")
"""The second week of June is no calendar week; nothing in a source can support it."""
KOREAN_WEEK = re.compile(
    rf"{KOREAN_YEAR}?(?:제\s*)?(?<!\d)(?P<n>\d{{1,2}})\s*주차(?:\s*\(\s*(?P<ny>\d{{4}})\s*년\s*\))?"
)


def latin_weeks(m: re.Match[str]) -> tuple[Week, ...]:
    if listed := m["short"] or m["long"]:
        return _listed(listed, int(m["y"]) if m["y"] else None)
    for week, year in (("n", "ny"), ("iw", "iy"), ("w", "wy")):
        if m[week]:
            return ((int(m[year]) if m[year] else None, int(m[week])),)
    return ()


def korean_weeks(m: re.Match[str]) -> tuple[Week, ...]:
    year = m["y"] or m["ny"]
    return ((int(year) if year else None, int(m["n"])),)


def _listed(text: str, year: int | None) -> tuple[Week, ...]:
    """ "6-8/2027": a week without its year has the year that follows in the list."""
    found: list[Week] = []
    following = year
    for week, own in reversed(re.findall(rf"({N})((?:{YEAR})?)", text)):
        following = int(own[-4:]) if own else following
        found.append((following, int(week)))
    return tuple(reversed(found))


# ── identifiers ─────────────────────────────────────────────────────────────
IDENTIFIER = re.compile(
    rf"(?<![{LATIN}\d])(?<![{LATIN}\d][-._])"
    rf"(?=[{LATIN}\d._-]*[A-Z])(?=[{LATIN}\d._-]*\d)"
    rf"[A-Z\d]+(?:[-._][A-Z\d]+)*"
    rf"(?![{LATIN}\d])(?![-._/][{LATIN}\d])"
)
FIGURE_LIKE = re.compile(
    r"\d+(?:[.,]\d+)*[A-Z]+"  # 5EUR: a number and its unit
    r"|(?:EUR|USD|GBP|CHF|KRW|JPY|CNY|RMB)[-.]?\d.*"
    r"|[A-Z]+[-.]?(?:19|20)\d{2}"  # SOP2028, FY2028
    r"|[QH]\d"
)


def is_identifier(token: str) -> bool:
    """A part number, standard or grade. A token with lower-case letters ("Cpk1.67", "12mm") is a figure."""
    return FIGURE_LIKE.fullmatch(token) is None
