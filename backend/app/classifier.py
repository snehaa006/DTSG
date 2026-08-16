"""Phase 4 — decide what a new fact does to the facts already stored.

Three outcomes:

  REINFORCE  the new fact restates one already held; raise its confidence
             rather than storing a second copy
  ADDITIVE   the new fact coexists with what is there; store it, touch nothing
  SUPERSEDE  the new fact makes one or more held facts untrue; store it, expire
             them, and link them to it

The distinction that actually matters is between ADDITIVE and SUPERSEDE, and it
is *not* "same subject and predicate". Whether a predicate can hold more than
one value at a time decides it:

    user speaks English  +  user speaks Hindi   -> ADDITIVE  (multi-valued)
    user lives_in Delhi  +  user lives_in Berlin -> SUPERSEDE (single-valued)

Both pairs share a subject and predicate. Treating that as contradiction would
silently delete the user's second language; treating it as coexistence would
leave them living in two cities. No amount of schema work decides this — it is a
judgement about the predicate, which is why an LLM makes the call.

The bias is toward ADDITIVE. A wrong ADDITIVE leaves a stale fact ACTIVE, which
retrieval ranking can still moderate and a later correction can fix. A wrong
SUPERSEDE expires something true, and while the row survives (nothing is ever
deleted), it drops out of "what's true now" until someone notices.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from .llm.base import LLMProvider, Tier
from .schemas import Fact, MemoryCandidate, Resolution

CLASSIFIER_SYSTEM = """\
You maintain a memory graph. Given one NEW fact and the EXISTING facts it might \
interact with, decide what the new fact does to them.

Choose exactly one:

REINFORCE — the new fact states the same thing as an existing fact. Same meaning, \
even if worded differently. Name the existing fact(s) it restates.

ADDITIVE — the new fact is compatible with everything existing. It adds \
information without making anything untrue. This includes facts that share a \
subject and predicate when that predicate can hold several values at once.

SUPERSEDE — the new fact makes one or more existing facts no longer true. Name \
every fact it invalidates.

The key question for ADDITIVE vs SUPERSEDE is whether the predicate can hold \
more than one value at the same time for that subject.

Single-valued (a new value replaces the old) — where someone lives, their \
current employer, their job title, their name, their age, a relationship status.
    "user lives_in Delhi" + new "user lives_in Berlin" -> SUPERSEDE
    "user works_at Acme"  + new "user works_at Globex" -> SUPERSEDE

Multi-valued (values accumulate) — languages spoken, skills, pets owned, \
allergies, interests, things liked or disliked, devices owned, places visited.
    "user speaks English" + new "user speaks Hindi"    -> ADDITIVE
    "user has_pet Rex"    + new "user has_pet Whiskers" -> ADDITIVE

Rules:
- Only cite existing facts from the list, using their exact id.
- SUPERSEDE requires a genuine contradiction, not just topical overlap. Two \
facts about the same area of life that can both be true are ADDITIVE.
- An explicit negation or correction ("actually", "no longer", "I stopped") is \
strong evidence for SUPERSEDE.
- If the existing list is empty, the answer is ADDITIVE.
- When genuinely unsure between ADDITIVE and SUPERSEDE, choose ADDITIVE. \
Keeping a stale fact is recoverable; expiring a true one hides it.
- Give a one-sentence reason naming the deciding consideration.\
"""


class Classification(BaseModel):
    """How a new fact relates to the existing ones."""

    resolution: Resolution = Field(
        description="REINFORCE, ADDITIVE, or SUPERSEDE."
    )
    target_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of the existing facts this applies to. Required for REINFORCE "
            "and SUPERSEDE; empty for ADDITIVE."
        ),
    )
    reasoning: str = Field(description="One sentence explaining the decision.")


def render_candidates(candidates: list[MemoryCandidate]) -> str:
    if not candidates:
        return "(none)"
    return "\n".join(
        f"- id={c.id} ({c.subject}, {c.predicate}, {c.object}) "
        f"confidence={c.confidence:.2f} since={c.valid_from:%Y-%m-%d}"
        for c in candidates
    )


def build_prompt(fact: Fact, candidates: list[MemoryCandidate]) -> str:
    return (
        f"NEW fact:\n- ({fact.subject}, {fact.predicate}, {fact.object})\n\n"
        f"EXISTING facts:\n{render_candidates(candidates)}"
    )


class Decision(BaseModel):
    """A classification with its targets resolved back to real memories."""

    resolution: Resolution
    targets: list[MemoryCandidate]
    reasoning: str


def _coerce(
    raw: Classification, candidates: list[MemoryCandidate]
) -> Decision:
    """Reconcile the model's answer with the candidates actually offered.

    The model can name an id that is not in the list, or return REINFORCE and
    SUPERSEDE with no target at all. Both are recoverable without a retry, and
    both must be handled before the result reaches the write path — a SUPERSEDE
    with no target would otherwise expire nothing while still writing a new
    fact, quietly producing the contradiction this phase exists to prevent.
    """
    by_id = {str(c.id): c for c in candidates}
    targets = [by_id[t] for t in raw.target_ids if t in by_id]

    resolution: Resolution = raw.resolution
    reasoning = raw.reasoning

    if resolution in ("REINFORCE", "SUPERSEDE") and not targets:
        # Nothing real to act on; storing the fact on its own is the safe read.
        resolution = "ADDITIVE"
        reasoning = f"{raw.resolution} named no known memory; treated as ADDITIVE. {reasoning}"

    if resolution == "ADDITIVE":
        targets = []
    elif resolution == "REINFORCE":
        # Reinforcing several rows at once would inflate confidence across facts
        # the user restated only one of; keep the closest match.
        targets = targets[:1]

    return Decision(resolution=resolution, targets=targets, reasoning=reasoning)


async def classify(
    provider: LLMProvider, fact: Fact, candidates: list[MemoryCandidate]
) -> Decision:
    """Classify a new fact against existing memories."""
    if not candidates:
        # No candidates means no possible conflict. Skipping the model here is
        # not just an optimization: it removes any chance of a hallucinated
        # target on the most common path, and it is most of the traffic.
        return Decision(
            resolution="ADDITIVE",
            targets=[],
            reasoning="No existing memories to conflict with.",
        )

    raw = await provider.complete_structured(
        tier=Tier.FAST,
        system=CLASSIFIER_SYSTEM,
        prompt=build_prompt(fact, candidates),
        schema=Classification,
    )
    return _coerce(raw, candidates)
