"""Intent without an LLM: the topic file and the rule plan (docs/design/chat.md §2, §5)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from autorfx.chat.models import Plan
from autorfx.chat.topics import load_topics, plan_by_rules
from chat_helpers import TOPICS
from conftest import REPO


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("문서끼리 다른 값이 있나요?", "conflicts"),
        ("독소조항을 알려줘", "red_flags"),
        ("RFQ 요약해 줘", "overview"),
        ("안녕하세요", "guide"),
        ("보증 기간은?", "lookup"),
    ],
)
def test_the_rule_plan_follows_the_wording(question: str, intent: str):
    """§2: without an LLM a question that names no topic is a lookup, worded exactly as asked."""
    assert plan_by_rules(question, TOPICS) == Plan(intent=intent, question=question, queries=())


def test_the_first_topic_that_matches_wins():
    """data/chat/topics.yaml: the order of the file decides."""
    both = "문서끼리 다른 독소조항"
    assert plan_by_rules(both, TOPICS).intent == "conflicts"
    assert plan_by_rules(both, TOPICS[::-1]).intent == "red_flags"
    assert plan_by_rules(both, ()).intent == "lookup"


def test_topics_are_read_in_file_order(tmp_path: Path):
    path = tmp_path / "topics.yaml"
    path.write_text(
        "topics:\n"
        "  - {intent: guide, label: 안내, patterns: [도움말]}\n"
        "  - {intent: red_flags, label: 불리한 조항, patterns: [독소, 위험]}\n",
        encoding="utf-8",
    )
    topics = load_topics(path)
    assert [t.intent for t in topics] == ["guide", "red_flags"]
    assert topics[1].label == "불리한 조항" and topics[1].patterns == ("독소", "위험")


def test_a_missing_topic_file_is_logged_and_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """§5: the chat works without the file."""
    caplog.set_level(logging.INFO)
    assert tuple(load_topics(tmp_path / "none.yaml")) == ()
    assert caplog.records


@pytest.mark.parametrize(
    "text",
    [
        "topics: 독소",
        "- just\n- a list",
        "topics:\n  - {intent: gossip, label: x, patterns: [y]}",
        "topics:\n  - {intent: guide, patterns: [y]}",
        "topics:\n  - {intent: guide, label: x, patterns: [y], extra: 1}",
        "topics: [",
    ],
)
def test_a_malformed_topic_file_is_an_error(tmp_path: Path, text: str):
    """§5, INV-9: an unreadable file is not silently an empty one."""
    path = tmp_path / "topics.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises((ValueError, yaml.YAMLError)):
        load_topics(path)


def test_the_committed_topic_file_routes_the_obvious_questions():
    """The crash-test question must not be taken for a question about contradictions."""
    topics = load_topics(REPO / "data" / "chat" / "topics.yaml")
    assert plan_by_rules("독소조항이 있나요?", topics).intent == "red_flags"
    assert plan_by_rules("문서끼리 값이 다른 곳은?", topics).intent == "conflicts"
    assert plan_by_rules("충돌 시험 요구사항은?", topics).intent == "lookup"


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("안녕", "guide"),
        ("안녕하세요!", "guide"),
        ("안녕하세요 납기 조건이 뭔가요?", "lookup"),
        ("도움말 파일은 어디에 있나요?", "lookup"),
        ("납기가 지연되면 불리한 점은?", "lookup"),
        ("우리한테 불리한 조항은?", "red_flags"),
        ("불리한 조항", "red_flags"),
        ("RFQ 요약", "overview"),
        ("RFQ 요약해줘", "overview"),
        ("RFQ 요약본 제출 기한은?", "lookup"),
        ("문서끼리 값이 다른 곳은?", "conflicts"),
        ("문서 간 불일치", "conflicts"),
        ("충돌 시험 기준은?", "lookup"),
    ],
)
def test_the_shipped_topics_route_real_questions(question: str, intent: str):
    """ENGINE-02: without a model a question about the documents must not be taken for a greeting."""
    topics = load_topics(Path(__file__).parents[2] / "data" / "chat" / "topics.yaml")
    assert plan_by_rules(question, topics).intent == intent
