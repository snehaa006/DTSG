"""Tests for the normalization layer and the /api/extract route.

Normalization is pure, so it is tested directly. The route is tested against a
stub provider — the point is to prove the route's plumbing and error mapping,
not to re-test Claude.
"""

from __future__ import annotations

import httpx
import pytest

from app.deps import get_llm
from app.extraction import (
    SELF_SUBJECT,
    ProposedFact,
    normalize,
    normalize_predicate,
    normalize_subject,
)
from app.llm.base import ExtractionError
from app.main import app


# --- normalization ---------------------------------------------------------


@pytest.mark.parametrize("term", ["I", "me", "My", "myself", "the user", "  i  "])
def test_first_person_collapses_to_one_subject(term: str) -> None:
    assert normalize_subject(term) == SELF_SUBJECT


def test_named_subject_is_preserved() -> None:
    assert normalize_subject("Acme Corp") == "Acme Corp"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("lives_in", "lives_in"),
        ("Lives In", "lives_in"),
        ("lives-in", "lives_in"),
        ("  RESIDES_IN ", "lives_in"),
        ("based_in", "lives_in"),
        ("moved_to", "lives_in"),
        ("works for", "works_at"),
        ("employed_at", "works_at"),
        ("likes", "prefers"),
        ("has  pet", "has_pet"),
        ("weird!!predicate", "weirdpredicate"),
    ],
)
def test_predicate_normalization(raw: str, expected: str) -> None:
    assert normalize_predicate(raw) == expected


def test_synonyms_converge_so_supersede_can_match() -> None:
    """The Phase 4 classifier matches on (subject, predicate); these must agree."""
    old = normalize_predicate("resides_in")
    new = normalize_predicate("moved_to")
    assert old == new == "lives_in"


def test_incomplete_triples_are_dropped() -> None:
    facts = normalize(
        [
            ProposedFact(subject="I", predicate="lives_in", object=""),
            ProposedFact(subject="", predicate="works_at", object="Acme"),
            ProposedFact(subject="I", predicate="", object="Berlin"),
            ProposedFact(subject="I", predicate="lives_in", object="Berlin"),
        ]
    )
    assert [(f.subject, f.predicate, f.object) for f in facts] == [
        (SELF_SUBJECT, "lives_in", "Berlin")
    ]


def test_duplicates_collapse_keeping_highest_confidence() -> None:
    facts = normalize(
        [
            ProposedFact(subject="I", predicate="lives_in", object="Berlin", confidence=0.6),
            ProposedFact(subject="me", predicate="resides_in", object="berlin", confidence=0.95),
        ]
    )
    assert len(facts) == 1
    assert facts[0].confidence == 0.95


def test_confidence_is_clamped_not_rejected() -> None:
    facts = normalize(
        [
            ProposedFact(subject="I", predicate="lives_in", object="Berlin", confidence=4.2),
            ProposedFact(subject="I", predicate="works_at", object="Acme", confidence=-1.0),
        ]
    )
    assert sorted(f.confidence for f in facts) == [0.0, 1.0]


def test_trailing_punctuation_is_stripped_from_object() -> None:
    facts = normalize([ProposedFact(subject="I", predicate="lives_in", object="Berlin.")])
    assert facts[0].object == "Berlin"


# --- route -----------------------------------------------------------------


class StubProvider:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[str] = []

    async def complete(self, **kwargs) -> str:  # pragma: no cover - unused here
        raise NotImplementedError

    async def complete_structured(self, *, prompt: str, schema, **kwargs):
        self.calls.append(prompt)
        if self._error is not None:
            raise self._error
        return schema(facts=self._result or [])


async def _post(payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/api/extract", json=payload)


@pytest.mark.asyncio
async def test_extract_returns_normalized_facts() -> None:
    stub = StubProvider(
        result=[
            {"subject": "I", "predicate": "moved_to", "object": "Berlin.", "confidence": 0.95},
            {"subject": "I", "predicate": "employed_at", "object": "Acme", "confidence": 0.9},
        ]
    )
    app.dependency_overrides[get_llm] = lambda: stub
    try:
        response = await _post({"text": "I moved to Berlin and joined Acme"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert {(f["predicate"], f["object"]) for f in body["facts"]} == {
        ("lives_in", "Berlin"),
        ("works_at", "Acme"),
    }


@pytest.mark.asyncio
async def test_empty_extraction_is_a_success_not_an_error() -> None:
    stub = StubProvider(result=[])
    app.dependency_overrides[get_llm] = lambda: stub
    try:
        response = await _post({"text": "what's the weather?"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"facts": [], "count": 0}


@pytest.mark.asyncio
async def test_missing_structured_output_maps_to_502() -> None:
    stub = StubProvider(error=ExtractionError("stop_reason=max_tokens"))
    app.dependency_overrides[get_llm] = lambda: stub
    try:
        response = await _post({"text": "hello"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_blank_text_is_rejected_before_the_model_is_called() -> None:
    stub = StubProvider(result=[])
    app.dependency_overrides[get_llm] = lambda: stub
    try:
        response = await _post({"text": ""})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert stub.calls == []
