"""Tests for classification and the write pipeline.

The model's *judgement* (is `speaks` multi-valued?) can only be checked against
the real API — scripts/check_classifier.py does that. These tests cover what
happens around it: reconciling the model's answer with reality, and the write
semantics each resolution implies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.classifier import Classification, _coerce, build_prompt, classify
from app.schemas import Fact, MemoryCandidate

USER = UUID("00000000-0000-0000-0000-000000000001")


def _candidate(subject="user", predicate="lives_in", obj="Delhi", similarity=1.0):
    return MemoryCandidate(
        id=uuid4(),
        user_id=USER,
        subject=subject,
        predicate=predicate,
        object=obj,
        status="ACTIVE",
        confidence=0.9,
        valid_from=datetime.now(timezone.utc),
        similarity=similarity,
    )


def _fact(subject="user", predicate="lives_in", obj="Berlin"):
    return Fact(subject=subject, predicate=predicate, object=obj, confidence=0.9)


class StubProvider:
    def __init__(self, classification: Classification) -> None:
        self._classification = classification
        self.calls = 0

    async def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def complete_structured(self, **kwargs):
        self.calls += 1
        return self._classification


# --- reconciling the model's answer ----------------------------------------


def test_supersede_naming_no_known_memory_degrades_to_additive() -> None:
    """A SUPERSEDE with no resolvable target would expire nothing while still
    writing the new fact — exactly the contradiction this phase prevents."""
    candidates = [_candidate()]
    raw = Classification(
        resolution="SUPERSEDE", target_ids=[str(uuid4())], reasoning="moved"
    )
    decision = _coerce(raw, candidates)

    assert decision.resolution == "ADDITIVE"
    assert decision.targets == []
    assert "treated as ADDITIVE" in decision.reasoning


def test_supersede_with_no_targets_at_all_degrades_to_additive() -> None:
    decision = _coerce(
        Classification(resolution="SUPERSEDE", target_ids=[], reasoning="x"),
        [_candidate()],
    )
    assert decision.resolution == "ADDITIVE"


def test_reinforce_with_unknown_target_degrades_to_additive() -> None:
    decision = _coerce(
        Classification(resolution="REINFORCE", target_ids=["nope"], reasoning="x"),
        [_candidate()],
    )
    assert decision.resolution == "ADDITIVE"


def test_unknown_ids_are_dropped_but_known_ones_survive() -> None:
    known = _candidate()
    raw = Classification(
        resolution="SUPERSEDE",
        target_ids=[str(known.id), str(uuid4())],
        reasoning="moved",
    )
    decision = _coerce(raw, [known])

    assert decision.resolution == "SUPERSEDE"
    assert [t.id for t in decision.targets] == [known.id]


def test_reinforce_keeps_only_the_closest_target() -> None:
    """Bumping several rows would inflate confidence on facts the user did not
    restate."""
    a, b = _candidate(obj="Delhi"), _candidate(obj="Mumbai")
    decision = _coerce(
        Classification(
            resolution="REINFORCE", target_ids=[str(a.id), str(b.id)], reasoning="x"
        ),
        [a, b],
    )
    assert len(decision.targets) == 1


def test_additive_never_carries_targets() -> None:
    c = _candidate()
    decision = _coerce(
        Classification(resolution="ADDITIVE", target_ids=[str(c.id)], reasoning="x"),
        [c],
    )
    assert decision.targets == []


def test_supersede_can_expire_several_memories() -> None:
    a, b = _candidate(obj="Delhi"), _candidate(obj="Mumbai")
    decision = _coerce(
        Classification(
            resolution="SUPERSEDE", target_ids=[str(a.id), str(b.id)], reasoning="x"
        ),
        [a, b],
    )
    assert decision.resolution == "SUPERSEDE"
    assert len(decision.targets) == 2


# --- classify --------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_candidates_skips_the_model_entirely() -> None:
    """The common path must not be able to hallucinate a target."""
    stub = StubProvider(Classification(resolution="SUPERSEDE", target_ids=["x"], reasoning="y"))
    decision = await classify(stub, _fact(), [])

    assert decision.resolution == "ADDITIVE"
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_candidates_are_classified_by_the_model() -> None:
    candidate = _candidate()
    stub = StubProvider(
        Classification(
            resolution="SUPERSEDE", target_ids=[str(candidate.id)], reasoning="moved"
        )
    )
    decision = await classify(stub, _fact(), [candidate])

    assert decision.resolution == "SUPERSEDE"
    assert decision.targets[0].id == candidate.id
    assert stub.calls == 1


# --- prompt ----------------------------------------------------------------


def test_prompt_includes_ids_so_targets_can_be_named() -> None:
    candidate = _candidate()
    prompt = build_prompt(_fact(), [candidate])

    assert str(candidate.id) in prompt
    assert "Delhi" in prompt and "Berlin" in prompt


def test_prompt_handles_an_empty_candidate_list() -> None:
    assert "(none)" in build_prompt(_fact(), [])
