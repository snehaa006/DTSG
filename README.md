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

**All seven phases complete.** Ingest, conflict resolution, temporal retrieval,
a timeline view of supersede chains, and a naive-RAG baseline to measure against.

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Supabase schema + FastAPI skeleton + Vercel chat UI | ✅ done |
| 2 | Fact extraction endpoint (LLM → subject/predicate/object) | ✅ done |
| 3 | Event log write path (immutable insert) | ✅ done |
| 4 | Conflict classifier (REINFORCE / ADDITIVE / SUPERSEDE) + write pipeline | ✅ done |
| 5 | Temporal retrieval with re-ranking | ✅ done |
| 6 | Frontend memory timeline showing supersede chains | ✅ done |
| 7 | Baseline naive-RAG endpoint for comparison | ✅ done |

> **Not yet verified against a live model.** The test suite stubs the LLM, so
> extraction quality and the classifier's single- vs multi-valued judgement are
> unproven. Run `scripts/list_models.py`, then `scripts/check_extraction.py` and
> `scripts/check_classifier.py` with a real key before trusting the graph —
> everything downstream ranks whatever those two write.

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/chat` | Full ingest: event → facts → classify → write |
| `POST /api/extract` | Facts from text, stateless (Phase 2) |
| `GET /api/events` | The immutable log, keyset-paginated |
| `GET /api/memories` | State graph, filterable by status |
| `POST /api/retrieve` | Temporal retrieval, modes `now` / `as_of` / `changes` |
| `GET /api/timeline` | Supersede chains for the timeline view |
| `POST /api/baseline/retrieve` | Naive RAG: cosine only |
| `POST /api/baseline/compare` | Both rankings for one query, side by side |

## Layout

```
supabase/migrations/   SQL migrations 0001-0004 (all applied to the DTSG project)
backend/               FastAPI service, deployed on Render
  app/config.py        Settings (env-driven)
  app/db.py            asyncpg pool
  app/schemas.py       Wire types
  app/routers/         health, chat
  app/llm/             Provider abstraction: Gemini generation + embeddings
frontend/              React + TypeScript + Vite, deployed on Vercel
render.yaml            Render blueprint
```

## Stack

| Piece | Choice |
| --- | --- |
| Database | Supabase Postgres + pgvector (`vector(1536)`, HNSW cosine index) |
| Backend | FastAPI on Render |
| Frontend | React + TypeScript + Vite on Vercel |
| Extraction / classification | Gemini, `MODEL_FAST` (default `gemini-2.5-flash`) |
| Answer generation | Gemini, `MODEL_SMART` — reserved, not called by any endpoint yet |
| Embeddings | `gemini-embedding-001` at 1536 dims |

Model calls go through `app/llm/base.py`, which names jobs by tier (`FAST` /
`SMART`) rather than by model, so the mapping can be repointed at another vendor
by writing one class. FAST runs on every message and dominates cost.

**Model IDs are configuration, not constants** — Google ships new ones often, so
the defaults may have moved on. Check what your key can actually reach:

```bash
cd backend && GEMINI_API_KEY=... .venv/bin/python scripts/list_models.py
```

It exits non-zero if a configured model is unavailable, so it works as a
preflight check.

**Embeddings are asymmetric.** Stored facts are embedded as
`RETRIEVAL_DOCUMENT`, search queries as `RETRIEVAL_QUERY` — same vector space,
better ranking. Conflict detection compares a new fact against stored facts, so
it uses the document role on both sides. `gemini-embedding-001` returns 3072
dimensions by default; the request asks for 1536 to match the `vector(1536)`
column, which the model supports natively (Matryoshka truncation) rather than
requiring a migration.

## Measuring against naive RAG (Phase 7)

`POST /api/baseline/retrieve` ranks by cosine similarity alone — no status
weighting, no decay, no conflict logic. `POST /api/baseline/compare` runs both
policies on one query and reports where they diverge.

**On fairness.** Both read the same `memories` rows, which might look like DTSG
is being handed a curated corpus. It isn't, and the equivalence is worth stating
precisely: a naive store never expires anything, so its corpus is "every fact
ever extracted" — and DTSG never deletes anything either, it only marks rows
EXPIRED. The row sets are identical. The baseline simply ignores the status and
time columns, exactly as a system that never wrote them would, which isolates
the retrieval policy as the only variable.

One caveat in the other direction: DTSG's REINFORCE collapses a restatement into
a confidence bump rather than a second row, so a truly naive store would hold a
few more near-duplicates. That works *against* DTSG here, making the measurement
conservative.

Run the benchmark — no database or API key:

```bash
cd backend && .venv/bin/python scripts/run_benchmark.py
# or with your own cases:
.venv/bin/python scripts/run_benchmark.py my_cases.json
```

Built-in cases, all ones where the stale fact is the equal-or-better textual
match:

```
correct top hit   dtsg 3/3   baseline 0/3
stale top hit     dtsg 0/4   baseline 3/4
```

The fourth case is a multi-valued predicate (`speaks English` + `speaks Hindi`)
where either answer is correct — it guards the opposite failure, since
over-eager superseding would have left only one of them ACTIVE.

## Temporal retrieval (Phase 5)

`POST /api/retrieve` scores every candidate as:

```
score = cosine_similarity × status_weight × exp(-λ · time_delta)
```

Three questions, one multiplication: *is this relevant*, *is it still true*,
*how stale is it*. The status term is what separates DTSG from ordinary RAG — a
superseded fact is not deleted and not filtered out, it is **discounted**
(default 0.15). So it never outranks its replacement, and never becomes
unreachable either.

Run the demo — no database or API key needed:

```bash
cd backend && .venv/bin/python scripts/check_retrieval.py
```

It scores a Delhi → Berlin move where **both facts have identical cosine
similarity (1.000)**, so plain vector search cannot tell them apart:

| Query | Berlin | Delhi |
| --- | --- | --- |
| "Where do I live?" (`mode=now`) | **0.9622** | 0.1443 |
| "Where have I lived?" (`expired_weight=1.0`) | 0.9622 | 0.9622 |
| "Where did I live in spring?" (`mode=as_of`, −60d) | 0.1500 | **1.0000** |

The last row is the one to notice: the order **inverts**, because at that
instant Berlin had not happened yet.

### Modes

| Mode | Eligible rows | Scored at |
| --- | --- | --- |
| `now` (default) | everything; superseded rows discounted | now |
| `as_of` | only facts that held at that instant | the given `as_of` |
| `changes` | only rows in a supersede chain, newest transition first | now |

`as_of` evaluates the status term **against that moment**, so a fact superseded
last week counts as fully current for a query about last month. That is what
makes "what did I say before X" a ranking question rather than a separate store.

### Re-ranking

Two stages, because no index can be built over a formula whose terms depend on
query time and caller-supplied constants. Postgres does approximate-nearest-
neighbour over the HNSW index; Python applies the full score to that pool.

The pool is deliberately over-fetched (6× the limit, minimum 40): top-K by
cosine is *not* top-K by score, and too small a pool silently drops rows that
would have won. `candidates_considered` in the response tells you whether the
pool was saturated.

### Knobs

`lambda_per_day` defaults to `ln(2)/180` — a 180-day half-life. Facts people
state about themselves age slowly, so aggressive decay buries correct answers
faster than they actually go stale. Both `lambda_per_day` and `expired_weight`
are per-request, so a test set can be swept without a redeploy.

Every response returns the three terms separately, not just the product: a
ranking you cannot decompose is one you cannot debug.

## The timeline view (Phase 6)

`GET /api/timeline` walks the supersede links into chains, oldest first, and the
frontend's **timeline** tab renders them: current values marked, superseded ones
struck through with their validity range, connected down the chain.

```
user · lives_in                    2 changes
  ○ Delhi   Jul 2025 → May 2026
  ○ Berlin  May 2026 → Aug 2026
  ● Lisbon  Aug 2026 → now
```

The walk follows `superseded_by` rather than `supersedes`, because that is the
complete edge: when one fact invalidates several, each old row records the
replacement, while the new row's single `supersedes` column can only name one of
them.

A fact that was never superseded comes back as a chain of one, so the UI needs a
single rendering path rather than separate "history" and "just a fact" cases.
Dangling links (a replacement outside the current page) stop the walk instead of
inventing entries, and cycles — which the write path should make impossible —
surface their rows rather than hanging or silently dropping them.

## Conflict resolution and the write pipeline (Phase 4)

`POST /api/chat` now runs the whole thing and reports what happened to each
fact:

```
message → event (committed) → facts → candidates → classification → writes
```

**The event commits in its own transaction, before anything derived runs.**
Extraction and classification both call an LLM, so both can fail on a rate limit
or a timeout. Sharing one transaction would roll back the raw message too — and
the raw message is the only thing that cannot be reconstructed. Memories can
always be rebuilt by replaying events; events cannot be rebuilt from anything.
So a downstream failure costs derived state, never history.

### The decision that matters

ADDITIVE vs SUPERSEDE is *not* "same subject and predicate":

```
user speaks English   + user speaks Hindi     → ADDITIVE   (multi-valued)
user lives_in Delhi   + user lives_in Berlin  → SUPERSEDE  (single-valued)
```

Both pairs share a subject and predicate. Treating that as contradiction would
silently delete the user's second language; treating it as coexistence would
leave them living in two cities. Nothing in the schema decides this — it is a
judgement about whether the predicate can hold several values at once, which is
why an LLM makes the call.

The bias is deliberately toward ADDITIVE. A wrong ADDITIVE leaves a stale fact
ACTIVE, which retrieval ranking moderates and a later correction fixes. A wrong
SUPERSEDE expires something true; the row survives, but it drops out of "what's
true now" until someone notices.

### Candidate search

Two arms, because they fail in opposite directions. **Exact `(subject,
predicate)`** is high-precision and always included regardless of vector
distance — if the user already has a `lives_in` fact, a new one must be
considered even when the two cities embed far apart. **Vector similarity**
(floored at 0.55) catches overlap the exact arm misses, such as an older
`works_at` against a new `works_as`.

### On SUPERSEDE

The new memory is inserted, then the targets are expired in the same
transaction: `status = 'EXPIRED'`, `valid_until = now()`, `superseded_by` → the
new row. The expiry is guarded on `status = 'ACTIVE'` so a retry cannot
overwrite a chain link written by the first attempt.

> `memories.supersedes` holds a single uuid, so when one fact invalidates
> several it records the closest one. The complete edge set lives on the other
> side — every expired row's `superseded_by` points at the replacement — so
> **`superseded_by` is the authoritative link**, and the one Phase 6 should walk.

Verify the model's judgement against your own cases:

```bash
cd backend
GEMINI_API_KEY=... .venv/bin/python scripts/check_classifier.py
```

Inspect the graph with `GET /api/memories?user_id=…` (add `status=EXPIRED` to
see what has been superseded).

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
GEMINI_API_KEY=... .venv/bin/python scripts/check_extraction.py
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
`DATABASE_URL`, `GEMINI_API_KEY`, and `CORS_ORIGINS` in the dashboard.

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
