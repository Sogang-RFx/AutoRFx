"""A tiny hand-made encoder and four clauses for dense-retrieval tests (no model download, no network).

Shared by ``unit/test_dense.py`` and ``unit/test_score.py``; ``tests`` is on the pytest path.
"""

from __future__ import annotations

import numpy as np

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
