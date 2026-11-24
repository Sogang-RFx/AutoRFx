"""The number check of chat answers (INV-3, CITE-01, docs/design/chat.md §3.2). Pure and deterministic.

A wrong figure that passes is shown as verified, so whatever is not clearly supported is refused:
the writer is then told which figure it was and may copy it as it stands.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from autorfx.chat.figures import Figure, Parts, figures, korean_value, readings
from autorfx.chat.units import compatible, unit_at

NUMBER_WORDS = {
    word: str(value)
    for value, words in enumerate(
        (
            "two zwei",
            "three drei",
            "four vier",
            "five fünf",
            "six sechs",
            "seven sieben",
            "eight acht",
            "nine neun",
            "ten zehn",
            "eleven elf",
            "twelve zwölf",
        ),
        start=2,
    )
    for word in words.split()
}
# A word that is only part of a number ("twenty-two", "two hundred", "three quarters") is not one.
BEFORE = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred")
AFTER = (
    r"hundred|thousand|millions?|billions?|dozen|point|and[\s-]+a|half|halves|thirds?|quarters|fifths"
    r"|hundert|tausend|millionen|mio|milliarden|mrd|komma|drittel|viertel|dutzend"
)
NUMBER_WORD = re.compile(
    "".join(rf"(?<!\b{word}[\s-])" for word in (*BEFORE, "thousand", "point", "komma"))
    + rf"(?<![\w-])({'|'.join(NUMBER_WORDS)})(?!\w)(?![\s-]+(?:{AFTER})(?!\w))",
    re.IGNORECASE,
)


K_UNIT = re.compile(r"\[k(?=[\s\]])")
"""A column or row in thousands: "[k units]". Not "[kg]"."""


def unsupported_numbers(text: str, sources: Sequence[str], *, context: Sequence[str] = ()) -> tuple[str, ...]:
    """The figures of ``text``, as written, that none of the cited ``sources`` supports.

    An identifier (a part number, a grade) states no value: that it stands in the evidence at all
    (``context``) is enough.
    """
    by_source = [figures(source) for source in sources]
    stated = tuple(figure for found in by_source for figure in found)
    worded = {
        (NUMBER_WORDS[m[1].casefold()], unit_at(source, m.start(), m.end()))
        for source in sources
        for m in NUMBER_WORD.finditer(source)
    }
    in_thousands = tuple(
        f for source, found in zip(sources, by_source, strict=True) if K_UNIT.search(source) for f in found
    )
    claims = figures(text)
    known: set[str] = set()
    if any(c.identifier for c in claims):
        known = {f.text for seen in (*sources, *context) for f in figures(seen) if f.identifier}
    return tuple(
        c.text
        for c in claims
        if not (
            c.text in known
            if c.identifier
            else _supported(c, stated, worded) or _in_thousands(c, in_thousands)
        )
    )


def _in_thousands(claim: Figure, facts: Sequence[Figure]) -> bool:
    """ "60 k" of "Vehicles [k units] | 60": copied with its unit, not multiplied."""
    plain = Figure(claim.thousands, digits=re.sub(r"\D", "", claim.thousands))
    return bool(claim.thousands) and any(f.values is None and _same_number(plain, f) for f in facts)


def _supported(claim: Figure, stated: Sequence[Figure], worded: set[tuple[str, str]]) -> bool:
    if claim.weeks:
        given = {week for fact in stated for week in fact.weeks}
        return all(any(number == w and year in (None, y) for y, w in given) for year, number in claim.weeks)
    if claim.written:
        return any(fact.written == claim.written for fact in stated)
    if claim.dates:
        return all(any(_states(fact, date) for fact in stated) for date in claim.dates)
    if claim.values is not None:
        # Equal sets: an ambiguous "1.250 Mio." may be copied, but not resolved.
        return bool(claim.values) and any(fact.values == claim.values for fact in stated)
    in_words = any(claim.text == number and compatible(claim.unit, unit) for number, unit in worded)
    return in_words or any(_same_number(claim, fact) for fact in stated)


def _states(fact: Figure, date: Parts) -> bool:
    """Every part the answer gives belongs to one date of the source; the source may give more."""
    year, month, day = date
    if year is not None and month is None and fact.digits:
        return fact.text == str(year) and compatible("year", fact.unit)
    if year is None and day is None:
        return date in fact.dates  # "12월" says less than any date of §3.2: only a copy of it passes
    return any(
        all(part is None or part == given for part, given in zip(date, stated, strict=True))
        for stated in fact.dates
    )


def _same_number(claim: Figure, fact: Figure) -> bool:
    if fact.values is not None:
        return fact.values == {korean_value(claim.text)}
    if fact.dates and not fact.written:
        return compatible("year", claim.unit) and any(
            claim.text == str(year) for year, _, _ in fact.dates if year is not None
        )
    if claim.digits != fact.digits or not fact.digits or not compatible(claim.unit, fact.unit):
        return False
    # The digits alone would let "1.67" support "16,7". A common value is needed, or, for what has
    # no value ("1.2.3"), the same groups.
    ours, theirs = readings(claim.text), readings(fact.text)
    return bool(ours & theirs) if ours or theirs else _groups(claim.text) == _groups(fact.text)


def _groups(token: str) -> list[str]:
    return re.findall(r"\d+", token)
