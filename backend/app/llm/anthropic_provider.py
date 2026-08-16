"""Claude-backed implementation of LLMProvider."""

from __future__ import annotations

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from ..config import Settings
from .base import Tier

from typing import TypeVar

T = TypeVar("T", bound=BaseModel)


class ExtractionError(RuntimeError):
    """The model returned no usable structured payload."""


class AnthropicProvider:
    def __init__(self, settings: Settings) -> None:
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._models = {
            Tier.FAST: settings.model_fast,
            Tier.SMART: settings.model_smart,
        }

    async def complete(
        self,
        *,
        tier: Tier,
        system: str,
        prompt: str,
        max_tokens: int = 1024,
    ) -> str:
        response = await self._client.messages.create(
            model=self._models[tier],
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in response.content if b.type == "text")

    async def complete_structured(
        self,
        *,
        tier: Tier,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 2048,
    ) -> T:
        response = await self._client.messages.parse(
            model=self._models[tier],
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        # `parsed_output` is None when the model refused or hit the token cap
        # before closing the JSON — both are real outcomes, not bugs.
        if response.parsed_output is None:
            raise ExtractionError(
                f"no structured output (stop_reason={response.stop_reason})"
            )
        return response.parsed_output
