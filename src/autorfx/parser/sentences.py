"""Sentence splitting and language detection.

This is the only sentence splitter in the code base (SPEC §4): sentence
numbers in citations depend on it, so two splitters would mean two answers to
"which sentence is the third one on page 30".

The splitter never changes text. It returns character spans; every non-blank
character of the input belongs to exactly one span.
"""

from __future__ import annotations

import re

from autorfx.models import Lang


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


ABBREVIATIONS = _words(
    "e.g i.e etc no nos nr ca bzw z.b vgl abs inkl incl excl ggf u.a d.h art fig inc ltd co corp vs approx "
    "min max mr ms mrs dr tel sec para ref rev vol dept est evtl usw zzgl o.g s.o s.u pos stk mind cf pp u.s "
    "ord std tech spec ext int gem lt sog bspw einschl ausschl mio mrd tsd"
)
MONTHS = _words(
    "januar februar märz april mai juni juli august september oktober november dezember "
    "january february march may june july october december"
)
INITIALS = re.compile(r"(?:[A-Za-z]\.)+[A-Za-z]")
ENUMERATION = re.compile(r"\d{1,3}|[A-Za-z]|[ivxIVX]{2,6}")
END = re.compile(r"[.!?]+[\"')\]”’]*")
EN_WORDS = _words("the and of to shall is in for be with or by must will this that are any not all may")
DE_WORDS = _words(
    "der die das und ist mit für den dem des nach sind wird muss bei von zu im eine einer auf nicht oder "
    "gemäß sowie werden durch zum zur"
)
WORD = re.compile(r"[A-Za-zÄÖÜäöüß]+")
HANGUL = re.compile(r"[가-힣]")


def _is_boundary(text: str, punct_start: int, end: int) -> bool:
    """True when the punctuation ending at ``end`` closes a sentence."""
    if text[punct_start] in "!?":
        return True
    before = text[:punct_start].split()
    following = text[end:].split(maxsplit=1)
    token = before[-1].lstrip("([„“\"'") if before else ""
    nxt = following[0] if following else ""
    if not token:
        return False
    if len(before) == 1 and ENUMERATION.fullmatch(token):
        # "1. The bidder shall ...": a list marker opens the paragraph; it is not a sentence.
        return False
    if token.casefold().rstrip(".") in ABBREVIATIONS or INITIALS.fullmatch(token):
        return False
    if token.isdigit():
        # "am 4. Dezember", "the 3. item": an ordinal, unless a capitalised non-month word follows.
        return bool(nxt) and nxt[0].isupper() and nxt.casefold().strip(".,") not in MONTHS
    return not (nxt and nxt[0].islower())


def split_sentences(text: str, lang: Lang = "unknown") -> list[tuple[int, int]]:
    """Character spans of the sentences of ``text``. ``lang`` is accepted for future rules."""
    spans: list[tuple[int, int]] = []
    start = len(text) - len(text.lstrip())
    for match in END.finditer(text):
        end = match.end()
        at_end = not text[end:].strip()
        if not at_end and (not text[end].isspace() or not _is_boundary(text, match.start(), end)):
            continue
        if end > start:
            spans.append((start, end))
        start = end + len(text[end:]) - len(text[end:].lstrip())
    tail = len(text.rstrip())
    if tail > start:
        spans.append((start, tail))
    return spans


def detect_language(text: str) -> Lang:
    """Language by stop-word vote. "mixed" when neither clearly wins, "unknown" for too little text."""
    if len(HANGUL.findall(text)) > max(10, len(text) // 5):
        return "ko"
    words = [w.casefold() for w in WORD.findall(text)]
    en = sum(w in EN_WORDS for w in words)
    de = sum(w in DE_WORDS for w in words)
    if en + de < 3:
        return "unknown"
    if en >= 2 * de:
        return "en"
    if de >= 2 * en:
        return "de"
    return "mixed"
