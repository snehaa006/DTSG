"""Shared singletons.

The Anthropic client holds a connection pool, so it is built once at startup
rather than per request. `get_llm` is written as a FastAPI dependency so tests
can override it with a stub provider and exercise the routers without a network
call or an API key.
"""

from __future__ import annotations

from typing import AsyncIterator

import asyncpg

from . import db
from .config import Settings, get_settings
from .llm.anthropic_provider import AnthropicProvider
from .llm.base import LLMProvider
from .llm.embeddings import EmbeddingClient

_llm: LLMProvider | None = None
_embeddings: EmbeddingClient | None = None


def init(settings: Settings) -> None:
    global _llm, _embeddings
    _llm = AnthropicProvider(settings)
    _embeddings = EmbeddingClient(settings)


async def shutdown() -> None:
    global _llm, _embeddings
    if _embeddings is not None:
        await _embeddings.aclose()
        _embeddings = None
    _llm = None


def get_llm() -> LLMProvider:
    if _llm is None:
        init(get_settings())
    assert _llm is not None
    return _llm


async def get_conn() -> AsyncIterator[asyncpg.Connection]:
    """Yield a pooled connection for the lifetime of one request."""
    async with db.acquire() as conn:
        yield conn


def get_embeddings() -> EmbeddingClient:
    if _embeddings is None:
        init(get_settings())
    assert _embeddings is not None
    return _embeddings
