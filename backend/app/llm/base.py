"""Provider-agnostic LLM interface.

DTSG makes two kinds of model call with very different cost profiles:

  FAST  - fact extraction and conflict classification, run on every message
  SMART - answer generation, run once per query

`Tier` names the job rather than the model so the mapping can move between
providers without touching call sites. Gemini is the current implementation
(`gemini_provider.py`); swapping in another vendor means writing one class that
satisfies these protocols.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Tier(str, Enum):
    FAST = "fast"
    SMART = "smart"


class ExtractionError(RuntimeError):
    """The model returned no usable structured payload.

    Lives here rather than in a provider module so callers can catch it without
    importing whichever vendor happens to be wired up.
    """


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

    async def complete_structured(
        self,
        *,
        tier: Tier,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 2048,
    ) -> T:
        """Return a response validated against `schema`.

        The model is constrained server-side, so this should not come back
        malformed. A provider without native structured output can implement it
        by asking for JSON in the prompt and validating client-side — with a
        retry, since that path *can* come back malformed.
        """
        ...


class EmbeddingProvider(Protocol):
    """Embeddings are asymmetric: a stored fact and a search query are embedded
    for different roles, and providers that support task hints rank better when
    told which is which. Both must land in the same vector space and at
    `settings.embedding_dim` dimensions to match the `vector(1536)` column.
    """

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed facts being stored, or compared against stored facts."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a retrieval query."""
        ...
