"""The unit of a figure, where it is one that is easily swapped (INV-3, docs/design/chat.md §3.2).

Only durations and currencies: "36 Tage" must not support "36개월". Any other unit, and a number without
one (a table states it in the header), is not compared.
"""

from __future__ import annotations

import re

_END = r"(?![A-Za-zÄÖÜäöüß])"
UNITS: tuple[tuple[str, str], ...] = (
    ("month", rf"개월|달(?=[ 을이의]|$)|(?i:months?|Monat(?:e|en|s)?){_END}"),
    (
        "day",
        rf"(?:영업|근무)?일|(?i:(?:(?:working|business|calendar)\s+)?days?)"
        rf"{_END}|(?i:(?:Arbeits|Kalender|Werk)?tag(?:e|en)?){_END}",
    ),
    ("week", rf"주(?![차당문요])|(?i:weeks?|Wochen?){_END}"),
    ("year", rf"년|(?i:years?|Jahr(?:e|en)?){_END}"),
    ("hour", rf"시간|(?i:hours?|hrs?|Stunden?){_END}|h{_END}"),
    ("EUR", rf"유로|€|EUR{_END}|Euro{_END}"),
    ("USD", rf"달러|US\$|\$|USD{_END}"),
    ("KRW", rf"원(?=[ 을이의]|$|입)|₩|KRW{_END}"),
)
AFTER = re.compile("[)\\s-]{0,3}(?:" + "|".join(f"(?P<{name}>{pattern})" for name, pattern in UNITS) + ")")
BEFORE = re.compile(r"(?:(?P<EUR>€|EUR)|(?P<USD>US\$|\$|USD)|(?P<KRW>₩|KRW))\s?$")


def unit_at(text: str, start: int, end: int) -> str:
    """The unit written directly after the figure, or the currency directly before it; else empty."""
    match = AFTER.match(text, end) or BEFORE.search(text[max(0, start - 5) : start])
    return (match.lastgroup or "") if match else ""


def compatible(a: str, b: str) -> bool:
    return not a or not b or a == b
