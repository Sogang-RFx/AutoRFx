"""Intent without an LLM: the wordings of ``data/chat/topics.yaml`` (docs/design/chat.md §2, §5)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import yaml

from autorfx.chat.models import Plan, Topic

log = logging.getLogger(__name__)

MAX_GREETING = 10
"""Longer than this, "안녕하세요 납기 조건이 뭔가요?" is a question that begins with a greeting."""


def load_topics(path: Path) -> tuple[Topic, ...]:
    """The topics in file order. No file means no topics; a malformed one is an error (INV-9)."""
    if not path.is_file():
        log.warning("chat topics not found, every question is a lookup without an LLM: %s", path.name)
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = raw.get("topics") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"{path.name}: 최상위는 topics 목록이어야 합니다")
    topics = tuple(Topic.model_validate(item) for item in items)
    # An empty wording is contained in every question.
    if any(not pattern.strip() for topic in topics for pattern in topic.patterns):
        raise ValueError(f"{path.name}: 빈 표현이 있습니다")
    return topics


def plan_by_rules(question: str, topics: Sequence[Topic]) -> Plan:
    """The first topic whose label the question is, or one of whose wordings it contains."""
    matching = (t.intent for t in topics if _matches(question.strip().casefold(), t))
    return Plan(intent=next(matching, "lookup"), question=question, queries=())


def _matches(question: str, topic: Topic) -> bool:
    if topic.intent == "guide" and len(question) > MAX_GREETING:
        return False
    return question == topic.label.casefold() or any(p.casefold() in question for p in topic.patterns)
