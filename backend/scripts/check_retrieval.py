#!/usr/bin/env python
"""Show what the temporal terms do to a ranking.

Needs no database and no API key — it scores a fixed scenario so the effect of
each term is visible on its own.

    cd backend
    .venv/bin/python scripts/check_retrieval.py

The scenario is the hard case on purpose: the user lived in Delhi, then moved to
Berlin, and **both facts are given identical cosine similarity**. Similarity
alone cannot separate them, so everything below comes from status and recency.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.retrieval import rerank  # noqa: E402
from app.schemas import Memory  # noqa: E402

USER = uuid4()
NOW = datetime.now(timezone.utc)
MOVED = NOW - timedelta(days=10)

berlin = Memory(
    id=uuid4(), user_id=USER, subject="user", predicate="lives_in", object="Berlin",
    status="ACTIVE", valid_from=MOVED,
)
delhi = Memory(
    id=uuid4(), user_id=USER, subject="user", predicate="lives_in", object="Delhi",
    status="EXPIRED", valid_from=NOW - timedelta(days=400), valid_until=MOVED,
)
# Same similarity for both: the ranking must come from somewhere else.
POOL = [(berlin, 1.0), (delhi, 1.0)]


def show(title: str, explanation: str, **kwargs) -> None:
    print(f"\n{title}\n  {explanation}")
    for r in rerank(POOL, limit=5, **kwargs):
        print(
            f"    {r.object:7} similarity={r.similarity:.3f} "
            f"status={r.status_weight:.2f} recency={r.recency:.3f} "
            f"-> score {r.score:.4f}"
        )


def main() -> None:
    print("Scenario: moved Delhi -> Berlin 10 days ago. Identical similarity (1.000).")

    show(
        '"Where do I live?"  (mode=now)',
        "Superseded facts are discounted, not hidden — Berlin wins clearly.",
        at=NOW,
    )
    show(
        '"Where have I lived?"  (expired_weight=1.0)',
        "Drop the penalty and history becomes fully reachable.",
        at=NOW,
        expired_weight=1.0,
    )
    show(
        '"Where did I live in the spring?"  (mode=as_of, 60 days ago)',
        "Scored as of then: Berlin had not happened yet, so the order inverts.",
        at=NOW - timedelta(days=60),
    )
    show(
        "No decay (lambda_per_day=0)",
        "Isolates the status term: recency is pinned at 1.0.",
        at=NOW,
        lambda_per_day=0.0,
    )

    print(
        "\nThe point: plain vector search sees one number (1.000) for both rows and "
        "\ncannot choose. The status and recency terms are what make the ranking "
        "\ncorrect — and reversible for historical questions."
    )


if __name__ == "__main__":
    main()
