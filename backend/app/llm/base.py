"""Provider-agnostic LLM interface.

DTSG makes two kinds of model call with very different cost profiles:

  FAST  - fact extraction and conflict classification, run on every message
  SMART - answer generation, run once per query

`Tier` names the job rather than the model so the mapping can move (to
Together.ai or Groq for open-weight models) without touching call sites.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class Tier(str, Enum):
    FAST = "fast"
    SMART = "smart"


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        tier: Tier,
        system: str,
        prompt: str,
        max_tokens: int = 1024,
    ) -> str:
        """Return the model's text response for a single-turn prompt."""
        ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, each of length `settings.embedding_dim`."""
        ...
