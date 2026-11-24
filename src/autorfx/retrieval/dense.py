"""Dense (embedding) retrieval over sentences (RAG-01, INV-4).

BM25 finds a sentence only when it shares a word, or a glossary wording, with the
question. A multilingual embedding model (BGE-M3 by default) also finds a German
or English sentence for a Korean question that the glossary does not cover
("견적서를 언제까지 내야 하나요?" → "Quotations must be received by ...").

The same sentences as the lexical index are embedded (headings are only the title
of the sentences below them, and the title is prepended for context). Like the
lexical index, a restricted team's question is scored against the rows of the
cost-free clauses only, so hidden text takes part in neither scoring nor answering.

The evidence gate. A sentence is evidence when its cosine similarity to the
question reaches ``min_score``. The right value depends on the model; measure it
on the development RFQs (``autorfx compare-search``) before relying on it.

Model choice (``EMBEDDING_MODEL``):

  * ``BAAI/bge-m3`` or any other sentence-transformers model id: runs locally.
    Needs ``uv sync --extra embedding`` (sentence-transformers, torch).
  * ``openai:text-embedding-3-small`` (or ``-large``): needs ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from autorfx.models import Clause, Group, ParsedSentence

log = logging.getLogger(__name__)

OPENAI_PREFIX = "openai:"
DEFAULT_MIN_SCORE = 0.55
"""Not calibrated: there is no answer key yet. With BAAI/bge-m3 on the development RFQs (10 questions,
hand-judged, top 3) the hybrid search refused nothing at 0.45; at 0.55 it refused most questions the
RFQs cannot answer but also dropped some correct sentences. Re-measure once an answer key exists."""
BATCH = 32


class EmbeddingUnavailable(RuntimeError):
    """The configured model cannot be loaded. The message says what to install or set."""


class Encoder(Protocol):
    """Turns texts into L2-normalised vectors (one row per text)."""

    name: str

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


def _normalise(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors[None, :]
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1.0, norms)


class SentenceTransformerEncoder:
    """A local model, e.g. ``BAAI/bge-m3`` (about 2.3 GB, downloaded once into the HF cache)."""

    def __init__(self, model: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise EmbeddingUnavailable(
                "sentence-transformers가 설치되지 않았습니다. `uv sync --extra embedding`을 실행하세요."
            ) from exc
        self.name = model
        self._model = SentenceTransformer(model)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors = self._model.encode(list(texts), batch_size=BATCH, normalize_embeddings=True)
        return _normalise(vectors)


class OpenAIEncoder:
    """``openai:text-embedding-3-small`` and the like. Uses the LLM key."""

    def __init__(self, model: str, api_key: str) -> None:
        from langchain_openai import OpenAIEmbeddings

        self.name = f"{OPENAI_PREFIX}{model}"
        self._client = OpenAIEmbeddings(model=model, api_key=api_key)  # type: ignore[arg-type]

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return _normalise(np.array(self._client.embed_documents(list(texts))))


def make_encoder(model: str, *, openai_api_key: str | None = None) -> Encoder:
    if model.startswith(OPENAI_PREFIX):
        if not openai_api_key:
            raise EmbeddingUnavailable(f"'{model}'에는 OPENAI_API_KEY가 필요합니다.")
        return OpenAIEncoder(model[len(OPENAI_PREFIX) :], openai_api_key)
    return SentenceTransformerEncoder(model)


class CachedEncoder:
    """Keeps the vectors of a whole corpus on disk, so reopening an RFQ does not re-embed it.

    The file name is a hash of the model and every text, so any change to the texts or the
    model is a different file. Questions are not cached.
    """

    def __init__(self, inner: Encoder, folder: Path) -> None:
        self.name = inner.name
        self._inner = inner
        self._folder = folder

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if len(texts) <= 1:
            return self._inner.encode(texts)
        digest = hashlib.sha256(self.name.encode())
        for text in texts:
            digest.update(b"\x00" + text.encode())
        path = self._folder / f"{digest.hexdigest()}.npy"
        if path.is_file():
            try:
                vectors = np.load(path)
                if vectors.shape[0] == len(texts):
                    return vectors
            except (OSError, ValueError):
                log.warning("embedding cache unreadable, recomputing: %s", path)
        vectors = self._inner.encode(texts)
        self._folder.mkdir(parents=True, exist_ok=True)
        np.save(path, vectors)
        return vectors


@dataclass(frozen=True, slots=True)
class DenseHit:
    sentence: ParsedSentence
    clause_id: str
    score: float
    """Cosine similarity, -1..1."""


def corpus_rows(clauses: Sequence[Clause]) -> list[tuple[ParsedSentence, str, str, bool]]:
    """``(sentence, clause_id, text to embed, cost-sensitive)`` for the sentences BM25 also indexes."""
    rows: list[tuple[ParsedSentence, str, str, bool]] = []
    for clause in clauses:
        body = [s for s in clause.sentences if s.role != "heading"] or list(clause.sentences)
        title = clause.own_title.strip()
        for sentence in body:
            if not any(character.isalpha() for character in sentence.text):
                continue
            text = f"{title}\n{sentence.text}" if title and title != sentence.text else sentence.text
            rows.append((sentence, clause.clause_id, text, clause.is_cost_sensitive))
    return rows


class DenseIndex:
    """Embedding search for one RFQ package, per access group."""

    def __init__(self, clauses: Sequence[Clause], encoder: Encoder, *, min_score: float = DEFAULT_MIN_SCORE):
        self.encoder = encoder
        self.min_score = min_score
        rows = corpus_rows(clauses)
        self._sentences = [row[0] for row in rows]
        self._clause_ids = [row[1] for row in rows]
        self._vectors = encoder.encode([row[2] for row in rows]) if rows else np.zeros((0, 1), np.float32)
        everything = np.arange(len(rows))
        self._rows: dict[Group, np.ndarray] = {
            "cost_visible": everything,
            "cost_restricted": np.array([i for i, row in enumerate(rows) if not row[3]], dtype=int),
        }

    @property
    def model(self) -> str:
        return self.encoder.name

    def search(
        self, question: str, *, group: Group, limit: int = 8, min_score: float | None = None
    ) -> tuple[DenseHit, ...]:
        """The best sentences at or above the gate, best first."""
        rows = self._rows["cost_visible" if group == "cost_visible" else "cost_restricted"]
        if not len(rows) or not question.strip():
            return ()
        gate = self.min_score if min_score is None else min_score
        query = self.encoder.encode([question])[0]
        # Only the rows this group may see are scored (INV-4).
        scores = self._vectors[rows] @ query
        order = np.argsort(-scores, kind="stable")[:limit]
        return tuple(
            DenseHit(self._sentences[rows[i]], self._clause_ids[rows[i]], float(scores[i]))
            for i in order
            if scores[i] >= gate
        )


def load_encoder(model: str | None, *, openai_api_key: str | None, cache_dir: Path | None) -> Encoder | None:
    """The configured encoder, or ``None`` (keyword search only) when none is set or it cannot load.

    A model that fails to load is logged and skipped: the service keeps answering with BM25 and the
    engine id says so, rather than refusing to start.
    """
    if not model:
        return None
    try:
        encoder: Encoder = make_encoder(model, openai_api_key=openai_api_key)
    except EmbeddingUnavailable as exc:
        log.warning("embedding model not used: %s", exc)
        return None
    except Exception:  # model download or load failure
        log.exception("embedding model '%s' could not be loaded; keyword search only", model)
        return None
    if cache_dir is not None:
        slug = "".join(c if c.isalnum() or c in "-._" else "_" for c in encoder.name)
        encoder = CachedEncoder(encoder, cache_dir / "embeddings" / slug)
    return encoder
