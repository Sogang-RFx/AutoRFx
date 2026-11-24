"""Dense and hybrid retrieval, with a tiny hand-made encoder (no model download, no network)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from auth_helpers import scope_for, visible_of
from autorfx.analysis import RuleOnlyProvider, analyze
from autorfx.chat.models import ChatContext
from autorfx.chat.service import answer_question
from autorfx.pipeline import _dense
from autorfx.retrieval import SearchIndex, compare
from autorfx.retrieval.dense import CachedEncoder, DenseIndex, load_encoder
from autorfx.rules import Ruleset
from contract_schema import assert_valid
from factories import make_file
from topic_encoder import CLAUSES, DEADLINE, PACKAGING, PRICE, QUALITY, TopicEncoder  # noqa: F401


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
    """INV-4: the hidden clause is not even scored for a restricted team (unscoped index, tools only)."""
    visible = index.search("가격은 얼마인가요?", group="cost_visible", mode="dense")
    assert "d01:2.2" in clause_ids(visible)
    for mode in ("dense", "hybrid"):
        hidden = index.search("가격은 얼마인가요?", group="cost_restricted", mode=mode)
        assert "d01:2.2" not in clause_ids(hidden)
    dense = index.dense
    assert dense is not None
    raw = dense.search("price EUR", group="cost_restricted", min_score=-1.0)
    assert all(hit.clause_id != "d01:2.2" for hit in raw)


def test_a_scoped_index_scores_only_its_own_rows(ruleset: Ruleset):
    """INV-4, auth-rbac §4: with a gate that lets everything through, the dense and the hybrid search of a
    scope return its own sentences and no others; the corpus is not embedded again."""
    encoder = TopicEncoder()
    index = SearchIndex(CLAUSES, ruleset, dense=DenseIndex(CLAUSES, encoder, min_score=-1.0))
    embedded = encoder.calls
    scoped = index.scoped(frozenset({DEADLINE.clause_id, QUALITY.clause_id}))
    assert encoder.calls == embedded, "the stored vectors are reused"
    assert scoped.dense is not None
    for mode in ("dense", "hybrid", None):
        found = scoped.search("price EUR packaging", mode=mode)
        assert clause_ids(found) == {DEADLINE.clause_id, QUALITY.clause_id}, mode
    assert clause_ids(index.search("price EUR", group="cost_visible", mode="dense")) == {
        c.clause_id for c in CLAUSES
    }, "the whole index is unchanged"
    assert not index.scoped(frozenset()).search("price EUR", mode="dense").hits


def test_a_subset_gives_the_same_ranking_as_an_index_of_those_clauses_alone(ruleset: Ruleset):
    """INV-4: ``DenseIndex.subset`` equals an index built from the subset, so nothing of the rest is felt."""
    ids = frozenset({DEADLINE.clause_id, PACKAGING.clause_id})
    whole = SearchIndex(CLAUSES, ruleset, dense=DenseIndex(CLAUSES, TopicEncoder(), min_score=0.5))
    alone = SearchIndex(
        [DEADLINE, PACKAGING],
        ruleset,
        dense=DenseIndex([DEADLINE, PACKAGING], TopicEncoder(), min_score=0.5),
    )
    for question in ("견적서를 언제까지 내야 하나요?", "부품을 어떤 상자에 담아서 보내야 하나요?", "price"):
        assert whole.scoped(ids).search(question) == alone.scoped(ids).search(question), question


def test_chat_answers_a_paraphrase_with_a_verified_citation(index: SearchIndex, ruleset: Ruleset):
    scope = scope_for("logistics", ruleset)
    analyses = analyze(CLAUSES, ruleset, RuleOnlyProvider())
    visible = visible_of(CLAUSES, analyses, scope, ruleset)
    shown = frozenset(c.clause_id for c in visible)
    assert "d01:3.2" in shown
    context = ChatContext(
        index=index.scoped(shown),
        clauses=visible,
        analyses=tuple(a for a in analyses if a.clause_id in shown),
        issues=(),
        files=(make_file("d01", "d01.pdf"),),
        topics=(),
    )
    data = answer_question(
        "부품을 어떤 상자에 담아서 보내야 하나요?", context=context, scope=scope, model=None
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


class _Offline(TopicEncoder):
    """Embeds nothing: the network, the key or the rate limit fails."""

    def __init__(self, fail_after: int = 0) -> None:
        super().__init__()
        self.fail_after = fail_after

    def encode(self, texts):
        if self.calls >= self.fail_after:
            raise ConnectionError("offline")
        return super().encode(texts)


def test_embedding_failure_while_loading_falls_back_to_keyword_search():
    """INV-12: an RFQ stays readable when the corpus cannot be embedded."""
    assert _dense(CLAUSES, _Offline(), None) is None


def test_embedding_failure_on_a_question_falls_back_to_keyword_search(ruleset: Ruleset):
    index = SearchIndex(CLAUSES, ruleset, dense=DenseIndex(CLAUSES, _Offline(fail_after=1), min_score=0.5))
    question = "IATF certification"
    assert index.search(question, group="cost_visible") == index.search_bm25(question, group="cost_visible")
    with pytest.raises(ConnectionError):
        index.search(question, group="cost_visible", mode="dense")  # a comparison asks for dense on purpose
