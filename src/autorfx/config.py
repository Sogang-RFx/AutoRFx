"""Settings (OPS-01).

Values come from the environment or ``.env``. Provider keys are optional: with
no LLM key the pipeline runs rule-based and says so; with no translation key
the translation stage is skipped (TRANS-03). Secrets are ``SecretStr`` so they
never appear in logs or ``repr``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ConfirmPolicy = Literal["always", "ambiguous_only", "never"]


DEFAULT_EMBEDDING_MODEL = "openai:text-embedding-3-large"
"""Chosen on 11/24 with the 30-question x 5-RFQ answer key (123 judged cells): hybrid search found 64 of
87 answers with it against 49 with BAAI/bge-m3, and overall accuracy was 82/123 against 71/123."""
FALLBACK_MIN_SCORE = 0.55
MODEL_MIN_SCORE = {
    "openai:text-embedding-3-large": 0.30,
    "openai:text-embedding-3-small": 0.45,
    "baai/bge-m3": 0.55,
}
"""Evidence gate per model (lower-case id). The scales differ: text-embedding-3-large scores lower than
BGE-M3 and finds almost nothing at 0.45. Values are the best hybrid gate of the 11/24 answer-key runs
(30 questions x 5 RFQs). For 3-large the sweep 0.10-0.70 peaked at 0.30 (82/123; 0.25: 79, 0.35: 76)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    data_dir: Path = Field(default=Path("data"), validation_alias="AUTORFX_DATA_DIR")

    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", validation_alias="OPENAI_MODEL")
    openai_translation_model: str | None = Field(default=None, validation_alias="OPENAI_TRANSLATION_MODEL")
    """Model for translation only. Unset means the judgement model (OPENAI_MODEL)."""
    openai_translation_effort: Literal["none", "low", "medium", "high"] | None = Field(
        default=None, validation_alias="OPENAI_TRANSLATION_EFFORT"
    )
    """Reasoning effort sent with translation calls. Unset sends nothing (gpt-4o does not accept it)."""
    deepl_api_key: SecretStr | None = Field(default=None, validation_alias="DEEPL_API_KEY")
    embedding_model: str | None = Field(default=DEFAULT_EMBEDDING_MODEL, validation_alias="EMBEDDING_MODEL")
    """Dense retrieval is added to BM25 with this model: ``openai:<model>`` (needs the LLM key; the default,
    OpenAI text-embedding-3-large) or a sentence-transformers id (``BAAI/bge-m3``, local,
    ``uv sync --extra embedding``). An empty ``EMBEDDING_MODEL=`` turns it off (keyword search only), and
    without an OpenAI key the default falls back to keyword search too."""
    embedding_min_score: float | None = Field(
        default=None, ge=-1.0, le=1.0, validation_alias="EMBEDDING_MIN_SCORE"
    )
    """Cosine similarity a sentence needs to count as evidence in dense search. Empty = the measured value
    of the model (``min_score_for``): the scale differs a lot between models."""

    chat_reasoning_effort: str | None = Field(default="low", validation_alias="OPENAI_CHAT_EFFORT")
    """How long a reasoning model thinks before it answers a question. Empty: not sent."""

    confirm_policy: ConfirmPolicy = Field(default="ambiguous_only", validation_alias="AUTORFX_CONFIRM_POLICY")
    llm_concurrency: int = Field(default=4, ge=1, le=16, validation_alias="AUTORFX_LLM_CONCURRENCY")

    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:3200", validation_alias="AUTORFX_CORS_ORIGINS"
    )
    allowed_hosts: str = Field(
        default="127.0.0.1,localhost,testserver", validation_alias="AUTORFX_ALLOWED_HOSTS"
    )
    """Host names the API answers for (DNS rebinding). "testserver" is the test client's name."""
    session_hours: float = Field(default=12.0, gt=0, le=168, validation_alias="AUTORFX_SESSION_HOURS")
    """Lifetime of a login, counted from the login; using it does not extend it."""
    cookie_secure: bool = Field(default=False, validation_alias="AUTORFX_COOKIE_SECURE")
    """Send the session cookie over HTTPS only. Must be true where the API is served over HTTPS."""
    max_files: int = Field(default=30, ge=1, validation_alias="AUTORFX_MAX_FILES")
    max_file_mb: int = Field(default=50, ge=1, validation_alias="AUTORFX_MAX_FILE_MB")

    @field_validator("openai_api_key", "deepl_api_key", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """An empty value or the ``.env.example`` placeholder means "not configured"."""
        if isinstance(value, str) and (not value.strip() or value.strip().lower().startswith("your_")):
            return None
        return value

    @field_validator(
        "embedding_model",
        "embedding_min_score",
        "openai_translation_model",
        "openai_translation_effort",
        "chat_reasoning_effort",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    def min_score_for(self, model: str | None) -> float:
        """The evidence gate for ``model``: the configured value, else the one measured for that model."""
        if self.embedding_min_score is not None:
            return self.embedding_min_score
        return MODEL_MIN_SCORE.get((model or "").strip().lower(), FALLBACK_MIN_SCORE)

    @field_validator("cors_origins")
    @classmethod
    def _named_origins(cls, value: str) -> str:
        """Requests carry the session cookie, so every allowed origin is named: no wildcard."""
        if "*" in value:
            raise ValueError("AUTORFX_CORS_ORIGINS에는 *를 쓸 수 없습니다. 화면 주소를 하나씩 적어 주세요.")
        return value

    @field_validator("openai_translation_effort", mode="after")
    @classmethod
    def _effort_needs_a_model(cls, value: str | None, info: ValidationInfo) -> str | None:
        """The effort belongs to the translation model: set both, or neither.

        A field validator, not a model validator: its error carries the effort value only, never the whole
        settings dict (which holds the API key). The model field is declared first, so it is validated first;
        when it failed it is absent from ``info.data`` and its own error is the one shown.
        """
        if value is None or "openai_translation_model" not in info.data:
            return value
        model = info.data["openai_translation_model"]
        if not model:
            raise ValueError("OPENAI_TRANSLATION_EFFORT needs OPENAI_TRANSLATION_MODEL: set both or neither")
        if model.lower().startswith("gpt-4"):
            # Every translation call would be rejected while the server looks healthy.
            raise ValueError("OPENAI_TRANSLATION_EFFORT is not accepted by gpt-4* models: leave it empty")
        return value

    @property
    def rules_dir(self) -> Path:
        return self.data_dir / "rules"

    @property
    def chat_topics_path(self) -> Path:
        """Outside ``rules_dir``: changing a wording must not invalidate stored analyses."""
        return self.data_dir / "chat" / "topics.yaml"

    @property
    def samples_dir(self) -> Path:
        """Development RFQ packages. The held-out set never lives here (see autorfx.guard)."""
        return self.data_dir / "raw" / "dev"

    @property
    def work_dir(self) -> Path:
        """Per-RFQ artifacts: stored originals, canonical PDFs, parsed and analysed output."""
        return self.data_dir / "work"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def translation_prices_path(self) -> Path:
        """USD per 1M tokens per translation model, for the estimated cost. Not a rule file."""
        return self.data_dir / "translation" / "prices.yaml"

    @property
    def answer_keys_dir(self) -> Path:
        return self.data_dir / "answer_keys"

    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        return tuple(o.strip() for o in self.cors_origins.split(",") if o.strip())

    @property
    def allowed_host_list(self) -> tuple[str, ...]:
        return tuple(h.strip() for h in self.allowed_hosts.split(",") if h.strip())

    @property
    def llm_connected(self) -> bool:
        return self.openai_api_key is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
