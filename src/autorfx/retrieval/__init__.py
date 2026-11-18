"""Retrieval and question answering: BM25 with a glossary, an evidence gate, and cited answers."""

from autorfx.retrieval.chat import answer_question
from autorfx.retrieval.index import Hit, SearchIndex, SearchResult

__all__ = ["Hit", "SearchIndex", "SearchResult", "answer_question"]
