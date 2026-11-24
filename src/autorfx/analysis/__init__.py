"""Clause analysis: rule candidates, providers, citation verification, the clause graph."""

from __future__ import annotations

from autorfx.analysis.candidates import Candidates, find_candidates
from autorfx.analysis.citation import verify
from autorfx.analysis.graph import analyze, assemble, cache_key
from autorfx.analysis.llm import PROMPT_VERSION, OpenAIProvider, openai_call
from autorfx.analysis.providers import (
    AnalysisProvider,
    CacheStore,
    ClauseInput,
    Judgement,
    MemoryCache,
    RuleOnlyProvider,
    ScriptedProvider,
    SqliteCache,
    TransientProviderError,
)
from autorfx.config import Settings
from autorfx.rules import Ruleset

__all__ = [
    "PROMPT_VERSION",
    "AnalysisProvider",
    "CacheStore",
    "Candidates",
    "ClauseInput",
    "Judgement",
    "MemoryCache",
    "OpenAIProvider",
    "RuleOnlyProvider",
    "ScriptedProvider",
    "SqliteCache",
    "TransientProviderError",
    "analyze",
    "assemble",
    "cache_key",
    "engine_ids",
    "find_candidates",
    "make_provider",
    "verify",
]


def make_provider(settings: Settings, ruleset: Ruleset) -> AnalysisProvider:
    """The LLM provider when a key is configured, otherwise the rule-only provider."""
    if settings.openai_api_key is None:
        return RuleOnlyProvider()
    call = openai_call(settings.openai_model, settings.openai_api_key.get_secret_value())
    return OpenAIProvider(ruleset, settings.openai_model, call)


def engine_ids(provider: AnalysisProvider) -> dict[str, object]:
    """How the analysis parts are computed, for the engine indicator (ENGINE-03)."""
    return {
        "analysis": provider.id,
        "translation": provider.id if provider.translates else "none",
        "llm_connected": provider.uses_llm,
    }
