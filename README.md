# DTSG — Dynamic Temporal State Graph

A memory engine for AI agents that treats memory as a **timeline of facts with
changing validity**, rather than a flat pile of retrievable text.

Two stores, with different mutability rules:

- **`events`** — an immutable log of raw user messages. Insert-only; never
  updated, never deleted.
- **`memories`** — a mutable state graph of subject–predicate–object facts, each
  carrying `status` (`ACTIVE` / `EXPIRED`), `confidence`, and
  `supersedes` / `superseded_by` links. Facts are expired and linked, never
  deleted, so history stays queryable.

## Status

**Phase 1 complete** — schema, FastAPI skeleton, and a React chat client that
POSTs messages end to end. `POST /api/chat` is transport only: it validates and
acknowledges, and does not yet write an event or extract a fact.

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Supabase schema + FastAPI skeleton + Vercel chat UI | ✅ done |
| 2 | Fact extraction endpoint (LLM → subject/predicate/object) | next |
| 3 | Event log write path (immutable insert) | |
| 4 | Conflict classifier (REINFORCE / ADDITIVE / SUPERSEDE) + write pipeline | |
| 5 | Temporal retrieval with re-ranking | |
| 6 | Frontend memory timeline showing supersede chains | |
| 7 | Baseline naive-RAG endpoint for comparison | |

## Layout

```
supabase/migrations/   SQL migrations (0001 is applied to the DTSG project)
backend/               FastAPI service, deployed on Render
  app/config.py        Settings (env-driven)
  app/db.py            asyncpg pool
  app/schemas.py       Wire types
  app/routers/         health, chat
  app/llm/             Provider abstraction: Claude + hosted embeddings
frontend/              React + TypeScript + Vite, deployed on Vercel
render.yaml            Render blueprint
```

## Stack

| Piece | Choice |
| --- | --- |
| Database | Supabase Postgres + pgvector (`vector(1536)`, HNSW cosine index) |
| Backend | FastAPI on Render |
| Frontend | React + TypeScript + Vite on Vercel |
| Extraction / classification | `claude-haiku-4-5` |
| Answer generation | `claude-sonnet-5` |
| Embeddings | OpenAI-compatible `/embeddings` API, 1536 dims |

Both model calls go through `app/llm/base.py`, which names jobs by tier
(`FAST` / `SMART`) rather than by model, so the mapping can be repointed at
Together.ai or Groq without touching call sites. The embedding client speaks the
OpenAI wire format, which most hosted providers implement.

## Local development

**Backend**

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # fill in DATABASE_URL at minimum
.venv/bin/uvicorn app.main:app --reload
```

`http://localhost:8000/docs` for the OpenAPI UI; `/health` reports database
reachability.

**Frontend**

```bash
cd frontend
npm install
cp .env.example .env      # VITE_API_BASE_URL defaults to localhost:8000
npm run dev
```

## Deployment

**Supabase** — migration `0001_dtsg_init.sql` is already applied to the `DTSG`
project. RLS is enabled on both tables with no policies: the browser never talks
to Postgres directly, and the API connects with the service role, which bypasses
RLS.

**Render** — `render.yaml` at the repo root builds from `backend/`. Set
`DATABASE_URL`, `ANTHROPIC_API_KEY`, `EMBEDDING_API_KEY`, and `CORS_ORIGINS` in
the dashboard.

> Use the Supabase **transaction pooler** URI (`aws-1-<region>.pooler.supabase.com`,
> port 6543), not `db.<ref>.supabase.co`. The direct host is IPv6-only and Render
> dials out over IPv4. The pooler also rules out prepared statements, which is
> why `db.py` sets `statement_cache_size=0`.

**Vercel** — root directory `frontend/`, framework preset Vite. Set
`VITE_API_BASE_URL` to the Render URL, then add that Vercel origin to
`CORS_ORIGINS` on Render.

## Retrieval design (Phase 5)

Candidates are scored as:

```
score = cosine_similarity × status_weight × exp(-λ × time_delta)
```

with `status_weight` 1.0 for `ACTIVE` and ~0.15 for `EXPIRED` — expired facts
stay reachable at a heavy discount, which is what makes "what did I say before
X" and "what changed" answerable without resurrecting stale facts into
present-tense answers.
