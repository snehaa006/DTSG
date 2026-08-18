"""Gemini embedding client.

Two things here are load-bearing.

**Output dimensionality.** `gemini-embedding-001` returns 3072 dimensions by
default, but the schema declares `vector(1536)`. Rather than migrate the column
and re-embed everything, the request asks for 1536 directly — the model is
trained so shorter prefixes remain useful (Matryoshka representation), so this
is a supported truncation, not a lossy hack.

**Task type.** A stored fact and a search query play different roles, and Gemini
ranks better when told which is which: facts are embedded as
RETRIEVAL_DOCUMENT, queries as RETRIEVAL_QUERY. Both land in the same space, so
cosine distance between them is still meaningful. Conflict detection compares a
*new fact* against *stored facts*, so it uses the document role on both sides.
"""

from __future__ import annotations

import math

from google import genai
from google.genai import types

from ..config import Settings


def _normalize(vector: list[float]) -> list[float]:
    """Scale to unit length.

    Google recommends this whenever a dimensionality below the 3072 default is
    requested. Cosine distance is scale-invariant so pgvector's `<=>` would
    behave the same either way, but normalizing keeps the stored vectors correct
    if the index ever moves to an inner-product or L2 operator.
    """
    magnitude = math.sqrt(sum(v * v for v in vector))
    if magnitude == 0:
        return vector
    return [v / magnitude for v in vector]


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.embedding_model
        self._dim = settings.embedding_dim

    async def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.aio.models.embed_content(
            model=self._model,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self._dim,
            ),
        )
        return [_normalize(list(e.values)) for e in (response.embeddings or [])]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], "RETRIEVAL_QUERY")
        if not vectors:
            raise RuntimeError("embedding service returned no vector for the query")
        return vectors[0]

    async def aclose(self) -> None:
        # The genai client manages its own transport; nothing to release.
        return None
