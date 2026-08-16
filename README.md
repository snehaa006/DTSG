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

**Phase 3 complete** — `POST /api/chat` now appends every message to the event
log, and immutability is enforced by the database rather than by convention.

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Supabase schema + FastAPI skeleton + Vercel chat UI | ✅ done |
| 2 | Fact extraction endpoint (LLM → subject/predicate/object) | ✅ done |
| 3 | Event log write path (immutable insert) | ✅ done |
| 4 | Conflict classifier (REINFORCE / ADDITIVE / SUPERSEDE) + write pipeline | next |
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

## The event log (Phase 3)

`POST /api/chat` appends the raw message to `events` before anything else
happens, and returns the `event_id`. Reads are available at
`GET /api/events?user_id=…` (newest first) and `GET /api/events/{id}`.

**Immutability is enforced in the database, not just intended.** Migration 0002
puts `BEFORE UPDATE` and `BEFORE DELETE` triggers on `events` that reject the
operation for every role, service role included. Before this, "append-only" was
a convention one careless query away from being false — and the temporal model
depends on it completely: if an event's text can be rewritten, every memory
derived from it becomes unverifiable and "what did I say before X" stops meaning
anything.

Real deletions do eventually become necessary (a GDPR erasure request, a test
teardown), so there is a deliberate escape hatch:

```sql
begin;
set local dtsg.allow_event_mutation = 'on';
delete from events where user_id = '...';
commit;
```

`set local` scopes the exemption to the transaction, so it cannot leak into the
pooled connection's next user. Application code never sets it. That is the
point: erasing history stays possible, but never accidental.

Listing uses keyset pagination on `(timestamp, id)` rather than `OFFSET`. The
log only grows at the head, so `OFFSET` would shift rows under a paging client;
and the id is in the key because two messages sent in the same millisecond share
a timestamp, which ordering by timestamp alone would drop or duplicate.

## Extraction (Phase 2)

`POST /api/extract` takes `{"text": "..."}` and returns normalized facts:

```json
{ "facts": [
    { "subject": "user", "predicate": "lives_in", "object": "Berlin", "confidence": 0.95 },
    { "subject": "user", "predicate": "works_at", "object": "Acme",   "confidence": 0.9  }
  ],
  "count": 2 }
```

Extraction runs in two stages, and the second one is load-bearing:

1. **Propose** — Haiku returns triples under a schema Claude enforces
   server-side, so the payload cannot come back malformed.
2. **Normalize** — subject and predicate are folded into canonical form.

Stage 2 exists because Phase 4 detects supersession by matching a new fact
against existing memories with the same `(subject, predicate)`. If the model
says `lives_in` on Monday and `resides_in` on Tuesday, nothing matches, nothing
supersedes, and the graph quietly accumulates contradictory ACTIVE facts. So
every first-person subject collapses to `user`, and predicates are snake-cased
and mapped through a synonym table (`resides_in`, `based_in`, `moved_to` →
`lives_in`). Extend `_PREDICATE_SYNONYMS` in `app/extraction.py` as you see new
drift on your own test set.

An empty `facts` list is a correct answer, not an error — questions and
greetings contain no durable facts.

To check the live model path (the test suite stubs it):

```bash
cd backend
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python scripts/check_extraction.py
```

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

Tests (no API key or database needed — the provider is stubbed):

```bash
.venv/bin/pip install -r requirements-dev.txt
DATABASE_URL=unused:// .venv/bin/python -m pytest
```

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
