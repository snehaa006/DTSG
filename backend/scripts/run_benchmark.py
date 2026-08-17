#!/usr/bin/env python
"""Run a test set through DTSG and the naive baseline, and score both.

    cd backend
    .venv/bin/python scripts/run_benchmark.py                  # built-in cases
    .venv/bin/python scripts/run_benchmark.py my_cases.json    # your own

Needs no database and no API key: cases carry their own facts and similarity
scores, so the only thing under test is the ranking policy. That is deliberate —
using real embeddings would make the result depend on the embedding model's
quality, which is not what this is measuring.

Case format (JSON list):

    [{"query": "where do I live?",
      "facts": [{"object": "Delhi",  "status": "EXPIRED",
                 "days_ago": 400, "expired_days_ago": 100, "similarity": 0.95},
                {"object": "Berlin", "status": "ACTIVE",
                 "days_ago": 100, "similarity": 0.90}],
      "expected": "Berlin"}]
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.retrieval import rerank  # noqa: E402
from app.schemas import Memory  # noqa: E402

NOW = datetime.now(timezone.utc)

# Each case is one a plain vector store gets wrong: the stale fact is the equal
# or better textual match, so similarity alone cannot resolve it.
BUILTIN_CASES = [
    {
        "query": "where do I live?",
        "facts": [
            {"object": "Delhi", "status": "EXPIRED", "days_ago": 400,
             "expired_days_ago": 100, "similarity": 0.95},
            {"object": "Berlin", "status": "ACTIVE", "days_ago": 100,
             "similarity": 0.90},
        ],
        "expected": "Berlin",
    },
    {
        "query": "where do I work?",
        "facts": [
            {"object": "Acme", "status": "EXPIRED", "days_ago": 600,
             "expired_days_ago": 30, "similarity": 0.93},
            {"object": "Globex", "status": "ACTIVE", "days_ago": 30,
             "similarity": 0.93},
        ],
        "expected": "Globex",
    },
    {
        "query": "what is my job title?",
        "facts": [
            {"object": "Engineer", "status": "EXPIRED", "days_ago": 900,
             "expired_days_ago": 200, "similarity": 0.97},
            {"object": "Staff Engineer", "status": "ACTIVE", "days_ago": 200,
             "similarity": 0.88},
        ],
        "expected": "Staff Engineer",
    },
    {
        "query": "which languages do I speak?",
        "facts": [
            {"object": "English", "status": "ACTIVE", "days_ago": 500,
             "similarity": 0.91},
            {"object": "Hindi", "status": "ACTIVE", "days_ago": 20,
             "similarity": 0.91},
        ],
        # Multi-valued: both are true, so either top hit is correct. This case
        # guards against the opposite failure — over-eager expiry would have
        # left only one of them ACTIVE.
        "expected": None,
    },
]


def build(fact: dict) -> tuple[Memory, float]:
    expired = fact["status"] == "EXPIRED"
    return (
        Memory(
            id=uuid4(),
            user_id=uuid4(),
            subject="user",
            predicate="x",
            object=fact["object"],
            status=fact["status"],
            valid_from=NOW - timedelta(days=fact["days_ago"]),
            valid_until=(
                NOW - timedelta(days=fact["expired_days_ago"]) if expired else None
            ),
        ),
        fact["similarity"],
    )


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else None
    cases = json.load(open(path)) if path else BUILTIN_CASES

    dtsg_correct = baseline_correct = scored = 0
    baseline_stale_tops = dtsg_stale_tops = 0

    for case in cases:
        pool = [build(f) for f in case["facts"]]

        # Baseline: cosine only. Ties broken by input order, as the database
        # would — which is exactly why a tie is dangerous without a status term.
        baseline = sorted(pool, key=lambda p: p[1], reverse=True)
        baseline_top = baseline[0][0]

        dtsg_top = rerank(pool, at=NOW, limit=len(pool))[0]

        baseline_stale_tops += baseline_top.status == "EXPIRED"
        dtsg_stale_tops += dtsg_top.status == "EXPIRED"

        expected = case.get("expected")
        if expected is not None:
            scored += 1
            dtsg_correct += dtsg_top.object == expected
            baseline_correct += baseline_top.object == expected

        mark = "ok  " if expected is None or dtsg_top.object == expected else "FAIL"
        print(f"{mark} {case['query']}")
        print(f"       baseline -> {baseline_top.object} ({baseline_top.status})")
        print(f"       dtsg     -> {dtsg_top.object} ({dtsg_top.status})")

    total = len(cases)
    print(f"\ncases: {total}   scored: {scored}")
    if scored:
        print(f"  correct top hit   dtsg {dtsg_correct}/{scored}   baseline {baseline_correct}/{scored}")
    print(f"  stale top hit     dtsg {dtsg_stale_tops}/{total}   baseline {baseline_stale_tops}/{total}")

    if scored and dtsg_correct < scored:
        sys.exit(1)


if __name__ == "__main__":
    main()
