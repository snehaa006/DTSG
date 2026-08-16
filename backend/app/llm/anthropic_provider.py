"""Claude-backed implementation of LLMProvider."""

from __future__ import annotations

from anthropic import AsyncAnthropic

from ..config import Settings
from .base import Tier


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
