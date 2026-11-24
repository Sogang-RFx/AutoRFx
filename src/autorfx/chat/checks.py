"""Checking a draft statement by statement (CITE-01, CITE-02, INV-3, docs/design/chat.md §3.5)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from autorfx.chat.figures import figures
from autorfx.chat.models import CitedText, Draft, Evidence, Statement
from autorfx.chat.numbers import unsupported_numbers
from autorfx.chat.tables import columns, header_of


@dataclass(frozen=True)
class Outcome:
    """What the model's drafts came to."""

    statements: tuple[CitedText, ...] = ()
    answered: bool = True
    complete: bool = True
    dropped: bool = False
    failed: bool = False
    """The model gave no draft at all."""


def checked(draft: Draft, evidence: Sequence[Evidence]) -> tuple[Outcome, list[str]]:
    """What passed, and what to tell the model about the rest."""
    if not draft.answered:
        return Outcome(answered=False), []
    by_number = {e.number: e for e in evidence}
    context = tuple(e.text for e in evidence)
    whole = [_completed(s, evidence, context) for s in draft.statements]
    results = [(s, _problem(s, by_number, evidence)) for s in whole]
    problems = [f"Statement {n}: {p}" for n, (_, p) in enumerate(results, start=1) if p]
    if not results:
        problems = ["There are no statements although answered is true."]
    passed = tuple(_cited(s, by_number) for s, problem in results if problem is None)
    return Outcome(passed, complete=draft.complete, dropped=bool(problems)), problems


def _problem(
    statement: Statement, by_number: Mapping[int, Evidence], evidence: Sequence[Evidence]
) -> str | None:
    """Why a statement is refused, in the words of the retry; None when it is verified (CITE-01)."""
    if not statement.text.strip():
        return "the text is empty."
    if not statement.citations:
        return "it cites no evidence sentence."
    if unknown := [n for n in statement.citations if n not in by_number]:
        return f"these evidence numbers do not exist: {', '.join(map(str, unknown))}."
    cited = [by_number[n] for n in statement.citations]
    if wrong := unsupported_numbers(statement.text, [e.text for e in cited]):
        return f"the sentences it cites do not state: {', '.join(wrong)}. Copy numbers as written."
    if _wrong_column(cited, statement.text, evidence):
        return "a value stands in another column of the table. Write each value after its own column."
    return None


def _completed(statement: Statement, evidence: Sequence[Evidence], context: Sequence[str]) -> Statement:
    """The statement, with the citations it needs but left out: a table header, the sentence of a part
    number. They become citations the user sees, so nothing is accepted on the strength of a sentence
    that is not shown (INV-3)."""
    by_number = {e.number: e for e in evidence}
    if not statement.citations or any(n not in by_number for n in statement.citations):
        return statement
    added = _with_header(statement, evidence, context)
    cited = [by_number[n].text for n in added.citations]
    stated = {f.text for text in cited for f in figures(text) if f.identifier}
    missing = {f.text for f in figures(added.text) if f.identifier} - stated
    sources = (_first_with(token, evidence) for token in sorted(missing))
    numbers = (*added.citations, *(n for n in sources if n is not None))
    return added.model_copy(update={"citations": tuple(dict.fromkeys(numbers))})


def _first_with(token: str, evidence: Sequence[Evidence]) -> int | None:
    return next((e.number for e in evidence if any(f.text == token for f in figures(e.text))), None)


def _with_header(statement: Statement, evidence: Sequence[Evidence], context: Sequence[str]) -> Statement:
    """Only when the header makes every figure true and its columns fit the values."""
    by_number = {e.number: e for e in evidence}
    cited = [by_number[n] for n in statement.citations]
    if not unsupported_numbers(statement.text, [e.text for e in cited], context=context):
        return statement
    headers = {h.number: h for e in cited if (h := header_of(e, evidence)) is not None}
    numbers = tuple(dict.fromkeys((*headers, *statement.citations)))
    if unsupported_numbers(statement.text, [by_number[n].text for n in numbers], context=context):
        return statement
    if not any(columns(statement.text, h, _rows_under(h, cited, evidence)).paired for h in headers.values()):
        return statement
    return statement.model_copy(update={"citations": numbers})


def _rows_under(header: Evidence, cited: Sequence[Evidence], evidence: Sequence[Evidence]) -> list[Evidence]:
    return [e for e in cited if (h := header_of(e, evidence)) is not None and h.number == header.number]


def _wrong_column(cited: Sequence[Evidence], text: str, evidence: Sequence[Evidence]) -> bool:
    rows = [e for e in cited if any(h.number == e.number for c in cited if (h := header_of(c, evidence)))]
    return any(columns(text, header, _rows_under(header, cited, evidence)).mismatch for header in rows)


def _cited(statement: Statement, by_number: dict[int, Evidence]) -> CitedText:
    loci = tuple(by_number[n].locus for n in dict.fromkeys(statement.citations))
    return CitedText(text=statement.text.strip(), citations=loci)
