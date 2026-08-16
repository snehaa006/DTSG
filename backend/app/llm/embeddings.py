"""Hosted embedding client.

Speaks the OpenAI `/embeddings` wire format, which Together.ai and most other
hosted providers also implement — so switching providers is a base-URL and
model-name change. The dimension must stay 1536 to match `vector(1536)` in the
schema; changing it means an ALTER on `memories.embedding` and a re-index.
"""

from __future__ import annotations

import httpx

from ..config import Settings


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.embedding_base_url,
            headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
            timeout=30.0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            "/embeddings",
            json={"model": self._settings.embedding_model, "input": texts},
        )
        response.raise_for_status()
        data = response.json()["data"]
        # The API does not promise ordered results; sort by index before use.
        return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]

    async def aclose(self) -> None:
        await self._client.aclose()
