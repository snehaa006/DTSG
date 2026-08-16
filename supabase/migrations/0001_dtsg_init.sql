-- DTSG Phase 1: immutable event log + mutable temporal state graph
-- Applied to Supabase project `DTSG` (sqplfrommcmmqitzqkss) on 2026-08-16.

create extension if not exists vector;

-- Immutable event log. Rows are inserted, never updated or deleted.
create table if not exists events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  raw_text text not null,
  timestamp timestamptz default now()
);

-- Mutable state graph. Facts are expired + linked, never deleted.
create table if not exists memories (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  subject text, predicate text, object text,
  status text check (status in ('ACTIVE','EXPIRED')) default 'ACTIVE',
  confidence float default 0.8,
  embedding vector(1536),
  source_event uuid references events(id),
  supersedes uuid references memories(id),
  superseded_by uuid references memories(id),
  valid_from timestamptz default now(),
  valid_until timestamptz
);

-- Retrieval + graph-traversal indexes.
create index if not exists events_user_ts_idx on events (user_id, timestamp desc);
create index if not exists memories_user_status_idx on memories (user_id, status);
create index if not exists memories_user_spo_idx on memories (user_id, subject, predicate);
create index if not exists memories_source_event_idx on memories (source_event);
create index if not exists memories_supersedes_idx on memories (supersedes);
create index if not exists memories_superseded_by_idx on memories (superseded_by);
create index if not exists memories_embedding_hnsw_idx
  on memories using hnsw (embedding vector_cosine_ops);

-- All DB access goes through FastAPI using the service_role key, which bypasses
-- RLS. Enabling RLS with no policies means the anon/publishable key can read
-- nothing directly, which is the posture we want: the browser never touches
-- Postgres, only the API.
alter table events enable row level security;
alter table memories enable row level security;
