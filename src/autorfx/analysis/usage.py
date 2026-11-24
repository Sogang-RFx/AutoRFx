"""Token usage of provider calls. Reasoning tokens are part of the output tokens and are not billed again."""

from __future__ import annotations

from autorfx.models import Model


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
