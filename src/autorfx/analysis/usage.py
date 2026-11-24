"""Token usage of provider calls and what it costs.

Reasoning tokens are part of the output tokens and are not billed again.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from autorfx.models import ClauseAnalysis, Model
from autorfx.rules import RulesError

PER_MILLION = Decimal(1_000_000)
OPENAI_PREFIX = "openai:"


class Usage(Model):
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def plus(self, other: Usage) -> Usage:
        return Usage(
            calls=self.calls + other.calls,
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class UsageMeter:
    """Adds up usages from several threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = Usage()

    def add(self, usage: Usage) -> None:
        with self._lock:
            self._total = self._total.plus(usage)

    def total(self) -> Usage:
        with self._lock:
            return self._total


class Price(Model):
    """USD per one million tokens."""

    input: Decimal
    cached_input: Decimal
    output: Decimal


FILE_KEYS = {"source", "checked", "unit", "models"}


def _decimal(value: object, where: str) -> Decimal:
    """A price is a quoted string: a YAML float would already have lost digits."""
    if not isinstance(value, str):
        raise RulesError(f"{where}: 단가는 따옴표로 감싼 문자열이어야 합니다")
    try:
        price = Decimal(value.strip())
    except InvalidOperation as exc:
        raise RulesError(f"{where}: 숫자가 아닙니다 ({value!r})") from exc
    if not price.is_finite() or price < 0:
        raise RulesError(f"{where}: 0 이상의 유한한 수여야 합니다 ({value!r})")
    return price


def load_prices(path: Path) -> dict[str, Price]:
    """Read the price file. Missing or malformed content is a :class:`RulesError`; nothing is defaulted."""
    if not path.is_file():
        raise RulesError(f"단가 파일이 없습니다: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RulesError(f"{path.name}: YAML 형식 오류: {exc}") from exc
    if not isinstance(data, dict):
        raise RulesError(f"{path.name}: 최상위는 매핑이어야 합니다")
    if unknown := sorted(str(k) for k in data.keys() - FILE_KEYS):
        raise RulesError(f"{path.name}: 알 수 없는 키 {', '.join(unknown)}")
    checked = data.get("checked")
    if not isinstance(checked, str) or not checked.strip():
        raise RulesError(f"{path.name}: 'checked'(단가를 확인한 날짜, 문자열)가 필요합니다")
    models = data.get("models")
    if not isinstance(models, dict) or not models:
        raise RulesError(f"{path.name}: 'models'는 비어 있지 않은 매핑이어야 합니다")
    fields = set(Price.model_fields)
    prices: dict[str, Price] = {}
    for model, entry in models.items():
        where = f"{path.name} {model}"
        if not isinstance(entry, dict):
            raise RulesError(f"{where}: input, cached_input, output을 가진 매핑이어야 합니다")
        if unknown := sorted(str(k) for k in entry.keys() - fields):
            raise RulesError(f"{where}: 알 수 없는 키 {', '.join(unknown)}")
        if absent := sorted(fields - entry.keys()):
            raise RulesError(f"{where}: 빠진 값 {', '.join(absent)}")
        prices[str(model)] = Price(**{k: _decimal(entry[k], f"{where}.{k}") for k in fields})
    return prices


def cost_usd(usage: Usage, model: str, prices: Mapping[str, Price]) -> Decimal | None:
    """Estimated cost of ``usage`` in USD, or None when the model has no price.

    Cached input is billed at the cached price and the rest at the input price; output tokens already
    include reasoning tokens. Cache-write charges and client retries that never reached the API are not known.
    """
    price = prices.get(model)
    if price is None:
        return None
    fresh = max(usage.input_tokens - usage.cached_input_tokens, 0)
    total = (
        fresh * price.input
        + usage.cached_input_tokens * price.cached_input
        + usage.output_tokens * price.output
    )
    return total / PER_MILLION


def usd_text(amount: Decimal) -> str:
    """A plain decimal string: no exponent, no trailing zeros."""
    return format(amount.normalize(), "f")


def translation_record(
    translation_id: str | None,
    usage: Usage,
    prices: Mapping[str, Price] | None,
    analyses: Sequence[ClauseAnalysis],
) -> dict[str, object]:
    """The ``translation`` entry of run.json: model, tokens, estimated cost (null if unpriced), counts."""
    cost = None
    if translation_id is not None and prices is not None:
        amount = cost_usd(usage, translation_id.removeprefix(OPENAI_PREFIX), prices)
        cost = None if amount is None else usd_text(amount)
    counts = Counter(analysis.translation_status for analysis in analyses)
    return {
        "model": translation_id or "none",
        "usage": usage.model_dump(),
        "cost_usd": cost,
        "counts": dict(sorted(counts.items())),
    }
