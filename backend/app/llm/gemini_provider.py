"""Gemini-backed implementation of LLMProvider.

Structured output is enforced server-side: passing a Pydantic model as
`response_schema` constrains generation, and `response.parsed` comes back as
that model. That is what lets extraction and classification skip JSON parsing
and retry loops entirely.
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from ..config import Settings
from .base import ExtractionError, Tier

T = TypeVar("T", bound=BaseModel)


class GeminiProvider:
    def __init__(self, settings: Settings) -> None:
        self._client = genai.Client(api_key=settings.gemini_api_key)
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
        response = await self._client.aio.models.generate_content(
            model=self._models[tier],
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        return response.text or ""

    async def complete_structured(
        self,
        *,
        tier: Tier,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 2048,
    ) -> T:
        response = await self._client.aio.models.generate_content(
            model=self._models[tier],
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        parsed = response.parsed
        # None when the model was blocked by a safety filter or ran out of
        # output tokens mid-JSON. Both are real outcomes, not bugs, and the
        # caller decides what to do about them.
        if parsed is None:
            raise ExtractionError(
                f"no structured output (finish_reason="
                f"{getattr(response.candidates[0], 'finish_reason', None) if response.candidates else None})"
            )
        return parsed
