"""Data models of the chat (docs/design/chat.md §3.1).

``Plan``, ``Statement`` and ``Draft`` are filled by the LLM. They have no defaults, so strict
structured output accepts the schema and the model always states every field.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from autorfx.models import Clause, ClauseAnalysis, ConsistencyIssue, FileRecord, Locus, Model
from autorfx.retrieval.index import ScopedSearchIndex

Intent = Literal["lookup", "overview", "red_flags", "conflicts", "guide"]


class Turn(Model):
    """One earlier exchange. It helps to understand the question and is never evidence (INV-3)."""

    question: str
    answer: str = ""


class Plan(Model):
    """The fields are written in this order: the intent is that of the resolved question, not of "그건?"."""

    question: str
    """The question with its pronouns resolved, so it can be understood without the conversation."""
    intent: Intent
    queries: tuple[str, ...]


class Statement(Model):
    text: str
    citations: tuple[int, ...]


class Draft(Model):
    answered: bool
    complete: bool
    statements: tuple[Statement, ...]


class Evidence(Model):
    number: int
    text: str
    locus: Locus
    file: str
    title: str
    contradicts: tuple[int, ...] = ()
    """Numbers of the evidence sentences that give another value for the same thing."""


class CitedText(Model):
    """A statement as it is shown: numbers replaced by positions (CITE-01)."""

    text: str
    citations: tuple[Locus, ...]


class Topic(Model):
    intent: Intent
    label: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class ChatContext:
    """Everything stored about one RFQ that a question may be answered from, already scoped to the account."""

    index: ScopedSearchIndex
    clauses: Sequence[Clause]
    analyses: Sequence[ClauseAnalysis]
    issues: Sequence[ConsistencyIssue]
    files: Sequence[FileRecord]
    topics: Sequence[Topic]
