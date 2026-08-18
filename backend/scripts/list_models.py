#!/usr/bin/env python
"""List the Gemini models your key can actually reach.

Model IDs move faster than this repo does, so `model_fast` / `model_smart` in
config.py are defaults, not facts. Run this to see what is really available and
override via env if they have shifted.

    cd backend
    GEMINI_API_KEY=... .venv/bin/python scripts/list_models.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google import genai  # noqa: E402

from app.config import Settings  # noqa: E402


def main() -> None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is not set")

    settings = Settings(database_url="unused://", gemini_api_key=key)
    client = genai.Client(api_key=key)

    generate: list[str] = []
    embed: list[str] = []
    for model in client.models.list():
        actions = set(getattr(model, "supported_actions", None) or [])
        name = (model.name or "").removeprefix("models/")
        if "embedContent" in actions:
            embed.append(name)
        if "generateContent" in actions:
            generate.append(name)

    print("generateContent:")
    for name in sorted(generate):
        marks = [
            label
            for label, configured in (
                ("model_fast", settings.model_fast),
                ("model_smart", settings.model_smart),
            )
            if name == configured
        ]
        print(f"  {name}{'   <- ' + ', '.join(marks) if marks else ''}")

    print("\nembedContent:")
    for name in sorted(embed):
        mark = "   <- embedding_model" if name == settings.embedding_model else ""
        print(f"  {name}{mark}")

    missing = [
        f"{label}={value}"
        for label, value, pool in (
            ("model_fast", settings.model_fast, generate),
            ("model_smart", settings.model_smart, generate),
            ("embedding_model", settings.embedding_model, embed),
        )
        if value not in pool
    ]
    if missing:
        print("\nNOT AVAILABLE TO THIS KEY: " + ", ".join(missing))
        print("Override with MODEL_FAST / MODEL_SMART / EMBEDDING_MODEL in the environment.")
        sys.exit(1)
    print("\nAll configured models are available.")


if __name__ == "__main__":
    main()
