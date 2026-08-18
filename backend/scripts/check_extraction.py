#!/usr/bin/env python
"""Run extraction against the real Gemini API and print what comes back.

The automated tests stub the provider, so this is what actually proves the live
path — schema acceptance, model behaviour, and prompt quality.

    cd backend
    GEMINI_API_KEY=... .venv/bin/python scripts/check_extraction.py

Pass your own sentences as arguments to try them instead of the samples.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings  # noqa: E402
from app.extraction import extract_facts  # noqa: E402
from app.llm.gemini_provider import GeminiProvider  # noqa: E402

SAMPLES = [
    # Plain multi-fact assertion.
    "I live in Bangalore and I work at Acme as a backend engineer.",
    # A change: should yield the NEW state only, ready for Phase 4 to supersede.
    "I moved from Delhi to Berlin last month.",
    # Hedged: confidence should drop.
    "I think I might switch to Rust next year.",
    # No durable facts: an empty list is the correct answer.
    "Hey, what's the weather like today?",
    # Predicate drift: should normalize onto lives_in / works_at.
    "These days I'm based in Lisbon, employed at Globex.",
]


async def main() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY is not set")

    settings = Settings(database_url="unused://", gemini_api_key=os.environ["GEMINI_API_KEY"])
    provider = GeminiProvider(settings)
    texts = sys.argv[1:] or SAMPLES

    for text in texts:
        facts = await extract_facts(provider, text)
        print(f"\n> {text}")
        if not facts:
            print("  (no durable facts)")
        for f in facts:
            print(f"  ({f.subject}, {f.predicate}, {f.object})  conf={f.confidence:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
