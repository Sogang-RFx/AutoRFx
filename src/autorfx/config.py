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

from pydantic import Field, SecretStr, field_validator
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
BGE-M3 and finds almost nothing at 0.45. Values are the best of the answer-key runs; the 3-large value was
the lowest gate tried, so re-measure below it."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True, populate_by_name=True
    )

    data_dir: Path = Field(default=Path("data"), validation_alias="AUTORFX_DATA_DIR")

    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", validation_alias="OPENAI_MODEL")
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

    confirm_policy: ConfirmPolicy = Field(default="ambiguous_only", validation_alias="AUTORFX_CONFIRM_POLICY")
    llm_concurrency: int = Field(default=4, ge=1, le=16, validation_alias="AUTORFX_LLM_CONCURRENCY")

    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:3200", validation_alias="AUTORFX_CORS_ORIGINS"
    )
    allowed_hosts: str = Field(
        default="127.0.0.1,localhost,testserver", validation_alias="AUTORFX_ALLOWED_HOSTS"
    )
    """Host names the API answers for (DNS rebinding). "testserver" is the test client's name."""
    max_files: int = Field(default=30, ge=1, validation_alias="AUTORFX_MAX_FILES")
    max_file_mb: int = Field(default=50, ge=1, validation_alias="AUTORFX_MAX_FILE_MB")

    @field_validator("openai_api_key", "deepl_api_key", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """An empty value or the ``.env.example`` placeholder means "not configured"."""
        if isinstance(value, str) and (not value.strip() or value.strip().lower().startswith("your_")):
            return None
        return value

    @field_validator("embedding_model", "embedding_min_score", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    def min_score_for(self, model: str | None) -> float:
        """The evidence gate for ``model``: the configured value, else the one measured for that model."""
        if self.embedding_min_score is not None:
            return self.embedding_min_score
        return MODEL_MIN_SCORE.get((model or "").strip().lower(), FALLBACK_MIN_SCORE)

    @property
    def rules_dir(self) -> Path:
        return self.data_dir / "rules"

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
