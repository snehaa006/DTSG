#!/usr/bin/env python
"""Check the classifier's judgement against the real Gemini API.

The unit tests cover reconciliation and write semantics, but the decision that
actually matters — is this predicate single-valued or multi-valued? — is the
model's, so it can only be checked live.

    cd backend
    GEMINI_API_KEY=... .venv/bin/python scripts/check_classifier.py

Exits non-zero if any case disagrees, so it can gate a deploy.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.classifier import classify  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm.gemini_provider import GeminiProvider  # noqa: E402
from app.schemas import Fact, MemoryCandidate  # noqa: E402

USER = uuid4()


def mem(predicate: str, obj: str) -> MemoryCandidate:
    return MemoryCandidate(
        id=uuid4(),
        user_id=USER,
        subject="user",
        predicate=predicate,
        object=obj,
        status="ACTIVE",
        confidence=0.9,
        valid_from=datetime.now(timezone.utc),
        similarity=0.9,
    )


def fact(predicate: str, obj: str) -> Fact:
    return Fact(subject="user", predicate=predicate, object=obj, confidence=0.9)


# (label, new fact, existing memories, expected resolution)
CASES = [
    # Single-valued predicates: the new value replaces the old.
    ("moved city", fact("lives_in", "Berlin"), [mem("lives_in", "Delhi")], "SUPERSEDE"),
    ("changed job", fact("works_at", "Globex"), [mem("works_at", "Acme")], "SUPERSEDE"),
    ("new title", fact("works_as", "Staff Engineer"), [mem("works_as", "Engineer")], "SUPERSEDE"),
    # Multi-valued predicates: values accumulate. Getting these wrong silently
    # deletes true facts, which is the expensive failure.
    ("second language", fact("speaks", "Hindi"), [mem("speaks", "English")], "ADDITIVE"),
    ("second pet", fact("has_pet", "Whiskers"), [mem("has_pet", "Rex")], "ADDITIVE"),
    ("another allergy", fact("allergic_to", "pollen"), [mem("allergic_to", "peanuts")], "ADDITIVE"),
    # Restatement.
    ("same fact reworded", fact("lives_in", "Berlin"), [mem("lives_in", "Berlin")], "REINFORCE"),
    # Unrelated predicate present: must not be dragged into a conflict.
    ("unrelated neighbour", fact("has_pet", "Rex"), [mem("lives_in", "Berlin")], "ADDITIVE"),
    # Several old values invalidated at once.
    (
        "replaces two",
        fact("lives_in", "Lisbon"),
        [mem("lives_in", "Delhi"), mem("lives_in", "Berlin")],
        "SUPERSEDE",
    ),
]


async def main() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY is not set")

    provider = GeminiProvider(
        Settings(database_url="unused://", gemini_api_key=os.environ["GEMINI_API_KEY"])
    )

    failures = 0
    for label, new_fact, candidates, expected in CASES:
        decision = await classify(provider, new_fact, candidates)
        ok = decision.resolution == expected
        failures += not ok
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} {label:22} expected={expected:10} got={decision.resolution:10}")
        print(f"       {decision.reasoning}")
        if decision.targets:
            print(f"       targets: {[t.object for t in decision.targets]}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} as expected")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
