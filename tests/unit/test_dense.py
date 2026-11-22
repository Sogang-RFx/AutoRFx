"""Dense and hybrid retrieval, with a tiny hand-made encoder (no model download, no network)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from autorfx.analysis import RuleOnlyProvider
from autorfx.retrieval import SearchIndex, answer_question, compare
from autorfx.retrieval.dense import CachedEncoder, DenseIndex, load_encoder
from autorfx.rules import Ruleset
from contract_schema import assert_valid
from factories import make_clause

# Each dimension is a topic; a text points at the topics whose words it contains, in any language.
# This is what a multilingual model does, reduced to something a test can reason about.
TOPICS = (
    ("submit", ("submission", "submitted", "received by", "abgabe", "언제까지", "제출")),
    ("packaging", ("packaging", "packed", "box", "verpackung", "상자", "담아")),
    ("price", ("price", "eur", "preis", "가격", "얼마")),
    ("quality", ("iatf", "certification", "자격", "품질")),
    ("warranty", ("warranty", "gewährleistung", "보증")),
)


class TopicEncoder:
    name = "test-topics"

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        rows = []
        for text in texts:
            low = text.casefold()
            row = [float(any(word in low for word in words)) for _, words in TOPICS] + [0.05]
            rows.append(row)
        vectors = np.array(rows, dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


DEADLINE = make_clause(
    "1.3",
    ["Quotations must be received by 15 December 2026.", "Late submissions are not considered."],
    title="Timeline",
)
PACKAGING = make_clause(
    "3.2",
    ["Die Teile sind in Mehrwegverpackung (KLT) anzuliefern."],
    title="Verpackung",
    first_sentence=3,
    lang="de",
)
PRICE = make_clause(
    "2.2",
    ["The target price is 1.2345 EUR per piece."],
    title="Target",
    first_sentence=4,
    is_cost_sensitive=True,
)
QUALITY = make_clause(
    "5.1",
    ["The supplier shall hold a valid IATF 16949 certification."],
    title="Quality",
    first_sentence=5,
)
CLAUSES = (DEADLINE, PACKAGING, PRICE, QUALITY)


@pytest.fixture(scope="module")
def index(ruleset: Ruleset) -> SearchIndex:
    return SearchIndex(CLAUSES, ruleset, dense=DenseIndex(CLAUSES, TopicEncoder(), min_score=0.5))


def clause_ids(result) -> set[str]:
    return {hit.clause_id for hit in result.hits}


def test_without_dense_the_default_is_bm25(ruleset: Ruleset):
    plain = SearchIndex(CLAUSES, ruleset)
    assert plain.dense is None
    assert plain.search("IATF certification", group="cost_visible") == plain.search_bm25(
        "IATF certification", group="cost_visible"
    )
    with pytest.raises(ValueError):
        plain.search("IATF", group="cost_visible", mode="dense")


def test_dense_finds_a_paraphrase_the_glossary_misses(index: SearchIndex):
    """'견적서를 언제까지 내야' shares no word or glossary entry with the English deadline clause."""
    question = "견적서를 언제까지 내야 하나요?"
    assert not index.search(question, group="cost_visible", mode="bm25").passed
    dense = index.search(question, group="cost_visible", mode="dense")
    assert dense.passed and clause_ids(dense) == {"d01:1.3"}
    hybrid = index.search(question, group="cost_visible")
    assert hybrid.passed and clause_ids(hybrid) == {"d01:1.3"}


def test_hybrid_keeps_bm25_hits_and_concepts(index: SearchIndex):
    result = index.search("IATF certification", group="cost_visible")
    assert result.passed and result.hits[0].clause_id == "d01:5.1"
    assert result.concepts == index.search_bm25("IATF certification", group="cost_visible").concepts
    assert [hit.rank for hit in result.hits] == list(range(1, len(result.hits) + 1))


def test_unrelated_question_stays_unanswered(index: SearchIndex):
    assert not index.search("대표이사 이름은?", group="cost_visible", mode="dense").passed


def test_restricted_group_never_sees_cost_sentences(index: SearchIndex):
    """INV-4: the hidden clause is not even scored for a restricted team."""
    visible = index.search("가격은 얼마인가요?", group="cost_visible", mode="dense")
    assert "d01:2.2" in clause_ids(visible)
    for mode in ("dense", "hybrid"):
        hidden = index.search("가격은 얼마인가요?", group="cost_restricted", mode=mode)
        assert "d01:2.2" not in clause_ids(hidden)
    dense = index.dense
    assert dense is not None
    raw = dense.search("price EUR", group="cost_restricted", min_score=-1.0)
    assert all(hit.clause_id != "d01:2.2" for hit in raw)


def test_chat_answers_a_paraphrase_with_a_verified_citation(index: SearchIndex):
    data = answer_question(
        "부품을 어떤 상자에 담아서 보내야 하나요?",
        index=index,
        clauses=CLAUSES,
        group="cost_restricted",
        provider=RuleOnlyProvider(),
    )
    assert_valid("chat", {"data": data, "provenance": {"chat": "real"}})
    assert data["status"] == "answered"
    assert data["answer"]["citation_status"] == "verified"
    assert data["answer"]["finding"]["citations"][0]["section_id"] == "3.2"


def test_cached_encoder_reuses_vectors(tmp_path: Path):
    inner = TopicEncoder()
    cached = CachedEncoder(inner, tmp_path / "emb")
    texts = ["packaging box", "IATF certification"]
    first = cached.encode(texts)
    second = cached.encode(texts)
    assert inner.calls == 1 and np.allclose(first, second)
    cached.encode(["one question"])  # questions are not cached
    assert inner.calls == 2 and len(list((tmp_path / "emb").iterdir())) == 1


def test_load_encoder_without_model_or_key():
    assert load_encoder(None, openai_api_key=None, cache_dir=None) is None
    assert load_encoder("openai:text-embedding-3-small", openai_api_key=None, cache_dir=None) is None


def test_compare_report(index: SearchIndex, tmp_path: Path):
    questions = [
        compare.Question("Q01", "견적서를 언제까지 내야 하나요?", "의역"),
        compare.Question("Q02", "IATF certification", "용어"),
    ]
    rows = compare.run(index, questions, top=2)
    assert {row.mode for row in rows} == {"bm25", "dense", "hybrid"}
    assert not any(row.mode == "bm25" and row.question.id == "Q01" for row in rows)
    report = compare.to_markdown(rows, questions, title="test", model="test-topics", gate=0.5)
    assert "근거 없음" in report and "Q02" in report
    compare.to_csv(rows, tmp_path / "out.csv")
    assert (tmp_path / "out.csv").read_text(encoding="utf-8-sig").startswith("질문ID")


def test_default_questions_file_loads():
    questions = compare.load_questions(
        Path(__file__).resolve().parents[2] / "data/questions/retrieval_ko.yaml"
    )
    assert len(questions) == 10 and len({q.id for q in questions}) == 10
