"""Section structure from paragraph labels (PARSE-02).

A paragraph opens a section when it starts with a label that fits the outline
seen so far. Two document styles are handled:

* ``decimal``: "3", "3.1", "3.1.2" (number and text may be on separate lines),
  "§ 4", and standard-clause heads such as "52.212-4".
* ``alpha``: roman chapters "II. TITLE IN CAPITALS" with letter sections
  "G. TITLE IN CAPITALS". Numbered items below them ("1.", "a.", "(1)") stay
  inside the section: they are list items of one clause, not clauses.

A number that is merely a value ("2.5 mm ± 0.15") never opens a section: it
must fit the outline (next sibling, first child, or next ancestor sibling) and
must not be followed by a unit. Table-of-contents lines are recognised by
dotted leaders or a trailing page number.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Mode = Literal["decimal", "alpha"]

UNIT = re.compile(
    r"^(mm|cm|m|kg|g|t|%|eur|usd|€|\$|h|°c|µm|ppm|nm|kn|mpa|bar|hz|stück|stk|pcs|pieces?|x|±|"
    r"werktagen?|tagen?|wochen?|monaten?|jahren?|days?|weeks?|months?|years?|hours?|minutes?|k|tsd|mio)\b",
    re.IGNORECASE,
)
DECIMAL = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})(\.?)\s+(\S.*)$", re.DOTALL)
PARAGRAPH_SIGN = re.compile(r"^§\s*(\d{1,3}[a-z]?)\.?\s+(\S.*)$", re.DOTALL)
STANDARD_CLAUSE = re.compile(r"^(?:FAR\s+|HSAR\s+)?(\d{2,4}\.\d{3}-\d{1,3})\b[\s.:–-]*(.*)$", re.DOTALL)
ROMAN_NUMBERED = re.compile(r"^((I{1,3}|IV|VI{0,3}|IX|X)\.(\d{1,2}))\s+(\S.*)$", re.DOTALL)
ROMAN = re.compile(r"^(I{1,3}|IV|VI{0,3}|IX|X)\.\s+([A-Z][A-Z0-9 ,/&()'’\-–]{4,})$")
ANY_LABEL = re.compile(
    r"^(?:[IVX]+\.\d{1,2}|\d{1,2}(?:\.\d{1,2}){0,4}\.?|\d{2,4}\.\d{3}-\d{1,3}|[A-Z]\.|[IVX]+\.)\s+\S"
)
INDEX_RUN = 5
LETTER = re.compile(r"^([A-Z])\.\s+([A-Z][A-Z0-9 ,/&()'’\-–]{2,})$")
LEADERS = re.compile(r"\.{5,}|…{2,}|(?:\. ){4,}")
TRAILING_PAGE = re.compile(r"\s\d{1,3}$")
ROMANS = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")
TITLE_MAX = 90


@dataclass(frozen=True)
class Heading:
    section_id: str
    title: str
    path: tuple[str, ...]
    label_len: int
    """Characters of the label at the start of the paragraph, including following spaces."""
    title_like: bool
    """True when the paragraph is only a heading, not a clause with its number in front."""


def find_index_runs(texts: Sequence[str]) -> set[int]:
    """Positions of paragraphs that list clauses rather than state them.

    A run of several consecutive labelled paragraphs that are all titles, with no text
    between them, is an index of clauses (a table of contents without dotted leaders) only
    when the document states at least one of those clauses later under the same label. A
    short numbered list that is the content itself ("2.1 Target price EUR 1.20") is never
    repeated, and dropping it would lose text that may have to be hidden (INV-8).
    """
    labels = [_label(text) if ANY_LABEL.match(text) else None for text in texts]
    listed: set[int] = set()
    run: list[int] = []
    for position, text in enumerate([*texts, ""]):
        entry = bool(ANY_LABEL.match(text)) and len(text) <= 260 and text.rstrip()[-1:] not in ".;:!?"
        if entry:
            run.append(position)
            continue
        if len(run) >= INDEX_RUN:
            later = {label for label in labels[run[-1] + 1 :] if label}
            if any(labels[index] in later for index in run):
                listed.update(run)
        run = []
    return listed


def _label(text: str) -> str:
    return text.split(None, 1)[0].rstrip(".")


def is_toc_line(text: str) -> bool:
    return bool(LEADERS.search(text))


def _title_like(rest: str) -> bool:
    rest = rest.strip()
    return 0 < len(rest) <= TITLE_MAX and rest[-1] not in ".;:!?" and rest[0].isupper()


def detect_mode(texts: Sequence[str]) -> Mode:
    """``alpha`` when the document has roman chapters with capital-letter sections."""
    short = [t for t in texts if len(t) <= 120 and not TRAILING_PAGE.search(t)]
    romans = sum(bool(ROMAN.match(t)) for t in short)
    letters = sum(bool(LETTER.match(t)) for t in short)
    return "alpha" if romans >= 2 and letters >= 3 else "decimal"


def _fits_outline(candidate: tuple[int, ...], last: tuple[int, ...] | None) -> bool:
    """True when ``candidate`` can follow ``last``: next sibling, first child or a later ancestor sibling."""
    if last is None:
        return candidate[0] <= 1 and all(part <= 1 for part in candidate[1:])
    for depth, part in enumerate(candidate):
        previous = last[depth] if depth < len(last) else None
        if previous is not None and part == previous:
            continue
        step_ok = part <= 1 if previous is None else part == previous + 1
        return step_ok and all(rest <= 1 for rest in candidate[depth + 1 :])
    return False


class Outliner:
    """Feeds on paragraphs in reading order and reports the ones that open a section."""

    def __init__(self, mode: Mode) -> None:
        self.mode = mode
        self._last: tuple[int, ...] | None = None
        self._chapters: list[str] = []
        self._letter: str | None = None
        self._titles: dict[str, str] = {}
        self._seen: Counter[str] = Counter()
        self._numbered: dict[str, int] = {}

    def _unique(self, section_id: str) -> str:
        self._seen[section_id] += 1
        count = self._seen[section_id]
        return section_id if count == 1 else f"{section_id}#{count}"

    def feed(self, text: str) -> Heading | None:
        text = text.replace("​", " ")
        if is_toc_line(text):
            return None
        return self._alpha(text) if self.mode == "alpha" else self._decimal(text)

    # ── alpha ───────────────────────────────────────────────────────────────
    def _alpha(self, text: str) -> Heading | None:
        if len(text) > 120 or TRAILING_PAGE.search(text):
            return None
        roman = ROMAN.match(text)
        expected = ROMANS[len(self._chapters)] if len(self._chapters) < len(ROMANS) else None
        if roman and roman.group(1) == expected:
            chapter = roman.group(1)
            self._chapters.append(chapter)
            self._letter = None
            return Heading(self._unique(chapter), roman.group(2).strip(), (chapter,), roman.start(2), True)
        letter = LETTER.match(text)
        if not letter:
            return None
        self._letter = letter.group(1)
        chapter = self._chapters[-1] if self._chapters else None
        base = f"{chapter}.{self._letter}" if chapter else self._letter
        path = (chapter, base) if chapter else (base,)
        return Heading(self._unique(base), letter.group(2).strip(), path, letter.start(2), False)

    # ── decimal ─────────────────────────────────────────────────────────────
    def _roman_numbered(self, text: str) -> Heading | None:
        """ "II.13 Title": a clause numbered inside a roman section; numbers must run on."""
        match = ROMAN_NUMBERED.match(text)
        if not match:
            return None
        chapter, number = match.group(2), int(match.group(3))
        previous = self._numbered.get(chapter, 0)
        # Clauses incorporated by reference are listed only in the index, so numbers may skip.
        if number <= previous or UNIT.match(match.group(4)):
            return None
        self._numbered[chapter] = number
        first_line = match.group(4).strip()
        title = first_line if len(first_line) <= 160 else ""
        return Heading(self._unique(match.group(1)), title, (chapter, match.group(1)), match.start(4), False)

    def _decimal(self, text: str) -> Heading | None:
        numbered = self._roman_numbered(text)
        if numbered:
            return numbered
        standard = STANDARD_CLAUSE.match(text)
        if standard:
            rest = standard.group(2).strip()
            title = rest if len(rest) <= 160 else ""
            return Heading(
                self._unique(standard.group(1)), title, (standard.group(1),), standard.start(2), False
            )
        sign = PARAGRAPH_SIGN.match(text)
        if sign:
            base = f"§{sign.group(1)}"
            rest = sign.group(2)
            like = _title_like(rest)
            return Heading(self._unique(base), rest.strip() if like else "", (base,), sign.start(2), like)
        match = DECIMAL.match(text)
        if not match:
            return None
        label, dot, rest = match.group(1), match.group(2), match.group(3)
        parts = tuple(int(p) for p in label.split("."))
        like = _title_like(rest)
        # The first label must start the numbering, unless it is plainly a chapter heading
        # (an annex may begin at chapter 3).
        opens_document = self._last is None and len(parts) == 1 and like
        if UNIT.match(rest) or not (opens_document or _fits_outline(parts, self._last)):
            return None
        if len(parts) == 1 and not dot and not like:
            return None  # "5 Werktage vor …": a bare integer opens a section only as a heading
        self._last = parts
        ancestors = tuple(".".join(label.split(".")[: i + 1]) for i in range(len(parts)))
        if like:
            self._titles[label] = rest.strip()
            title = rest.strip()
        else:
            title = next((self._titles[a] for a in reversed(ancestors[:-1]) if a in self._titles), "")
        return Heading(self._unique(label), title, ancestors, match.start(3), like)
