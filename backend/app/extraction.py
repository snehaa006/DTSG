"""Phase 2 — turn a raw message into subject-predicate-object facts.

The hard requirement here is not "get triples out of text", it is **get triples
that Phase 4 can compare to each other**. Write-time invalidation works by
finding existing memories that talk about the same thing as a new fact; if the
model says `lives_in` today and `resides_in` tomorrow, nothing matches, nothing
supersedes, and the graph accumulates contradictory ACTIVE facts.

So extraction is deliberately two stages:

  1. the model proposes triples (structured output, schema-enforced)
  2. we normalize subject and predicate into a canonical form

Stage 2 is not decoration. It is the reason the supersede chain in Phase 4 will
have anything to link.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from .llm.base import LLMProvider, Tier
from .schemas import Fact

# The subject every first-person statement collapses to. Without this, "I",
# "me", and "my" become distinct graph nodes and no two facts about the speaker
# ever line up.
SELF_SUBJECT = "user"

_SELF_TERMS = {"i", "me", "my", "myself", "mine", "the user", "user", "speaker"}

# Canonical predicates for the things people state about themselves most often.
# The model is told to prefer these but may coin new ones; anything it coins is
# still normalized to snake_case, so the vocabulary grows in a consistent shape.
CANONICAL_PREDICATES = (
    "lives_in",
    "works_at",
    "works_as",
    "prefers",
    "dislikes",
    "owns",
    "uses",
    "is_learning",
    "is_named",
    "has_goal",
    "has_pet",
    "allergic_to",
    "speaks",
    "born_in",
    "studies_at",
)

# Drift observed from LLM extraction: different surface verbs for one relation.
# Mapping them onto a single canonical predicate is what lets a later
# "I moved to Berlin" supersede an earlier "I live in Delhi".
_PREDICATE_SYNONYMS = {
    "resides_in": "lives_in",
    "located_in": "lives_in",
    "lives_at": "lives_in",
    "based_in": "lives_in",
    "moved_to": "lives_in",
    "employed_at": "works_at",
    "works_for": "works_at",
    "employed_by": "works_at",
    "job_title": "works_as",
    "occupation": "works_as",
    "role_is": "works_as",
    "likes": "prefers",
    "favorite": "prefers",
    "prefers_to_use": "prefers",
    "hates": "dislikes",
    "learning": "is_learning",
    "name_is": "is_named",
    "called": "is_named",
    "goal_is": "has_goal",
    "wants_to": "has_goal",
    "studies_in": "studies_at",
    "attends": "studies_at",
}

EXTRACTION_SYSTEM = f"""\
You extract durable facts from a user's message for a long-term memory system.

Return subject-predicate-object triples. A triple earns its place only if it \
would still be worth knowing in a week.

Extract:
- stable attributes, relationships, preferences, possessions, and commitments
- facts stated about the speaker or about people, places, and things they mention

Do NOT extract:
- questions, greetings, or requests for the assistant to do something
- momentary states ("I'm tired right now")
- facts about the assistant itself
- anything the user is asking about rather than asserting

Rules:
- subject: use "{SELF_SUBJECT}" for anything the speaker asserts about themselves. \
Otherwise use the specific entity name.
- predicate: snake_case. Prefer one of these when it fits: \
{", ".join(CANONICAL_PREDICATES)}. Coin a new snake_case predicate only when \
none of those fit.
- object: the value alone, concise, no trailing punctuation. Keep the surface \
form the user used for names and places.
- Split compound statements into separate triples. "I moved to Berlin and \
joined Acme" is two facts.
- When the user states a change ("I moved from Delhi to Berlin"), extract the \
NEW state ("{SELF_SUBJECT}", "lives_in", "Berlin"). Do not extract the old \
state; superseding the old fact is handled downstream.
- confidence: 0.9-1.0 for flat assertions, 0.5-0.7 for hedged ones \
("I think", "probably", "might"), lower for guesses.

If the message contains no durable facts, return an empty list. An empty list \
is a correct and common answer.\
"""


# These class names, docstrings, and field descriptions are serialized into the
# JSON schema the model receives, so they are written for the model to read.
class ProposedFact(BaseModel):
    """A single durable fact stated in the message."""

    subject: str = Field(
        description=f'Who or what the fact is about. Use "{SELF_SUBJECT}" for the speaker.'
    )
    predicate: str = Field(
        description="The relation, in snake_case, e.g. lives_in, works_at, prefers."
    )
    object: str = Field(description="The value, concise and without trailing punctuation.")
    confidence: float = Field(
        default=0.9,
        description="0.0-1.0. High for flat assertions, lower for hedged ones.",
    )


class ExtractionResult(BaseModel):
    """All durable facts found in the message; empty when there are none."""

    facts: list[ProposedFact]


def normalize_subject(subject: str) -> str:
    cleaned = subject.strip().strip(".,;:").strip()
    if cleaned.lower() in _SELF_TERMS:
        return SELF_SUBJECT
    return cleaned


def normalize_predicate(predicate: str) -> str:
    """Fold a proposed predicate into canonical snake_case.

    Applied to every predicate, including ones already in CANONICAL_PREDICATES,
    so the output shape is uniform whether the model followed instructions or
    invented something.
    """
    cleaned = predicate.strip().lower()
    cleaned = re.sub(r"[\s\-]+", "_", cleaned)
    cleaned = re.sub(r"[^a-z0-9_]", "", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return _PREDICATE_SYNONYMS.get(cleaned, cleaned)


def normalize_object(obj: str) -> str:
    return obj.strip().strip(".,;:").strip()


def _dedupe(facts: list[Fact]) -> list[Fact]:
    """Collapse triples that are identical after normalization.

    A single message can restate the same fact ("I'm in Berlin now, living in
    Berlin"); keep the highest-confidence copy.
    """
    best: dict[tuple[str, str, str], Fact] = {}
    for fact in facts:
        key = (fact.subject, fact.predicate, fact.object.lower())
        existing = best.get(key)
        if existing is None or fact.confidence > existing.confidence:
            best[key] = fact
    return list(best.values())


def normalize(proposed: list[ProposedFact]) -> list[Fact]:
    facts: list[Fact] = []
    for raw in proposed:
        subject = normalize_subject(raw.subject)
        predicate = normalize_predicate(raw.predicate)
        obj = normalize_object(raw.object)
        # A triple missing any leg is not a fact; drop rather than persist a
        # half-edge the graph can never match against.
        if not (subject and predicate and obj):
            continue
        facts.append(
            Fact(
                subject=subject,
                predicate=predicate,
                object=obj,
                # Clamp rather than reject: an out-of-range confidence is the
                # model misjudging a scale, not a reason to lose the fact.
                confidence=min(1.0, max(0.0, raw.confidence)),
            )
        )
    return _dedupe(facts)


async def extract_facts(provider: LLMProvider, text: str) -> list[Fact]:
    """Extract normalized facts from one message. May legitimately return []."""
    result = await provider.complete_structured(
        tier=Tier.FAST,
        system=EXTRACTION_SYSTEM,
        prompt=text,
        schema=ExtractionResult,
    )
    return normalize(result.facts)
