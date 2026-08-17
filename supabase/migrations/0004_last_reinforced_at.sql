-- Give reinforcement a temporal effect.
--
-- Phase 4's REINFORCE raises a memory's confidence. But the Phase 5 scoring
-- formula is `cosine * status_weight * exp(-lambda * time_delta)` — confidence
-- does not appear in it. So as things stand, restating a fact ten times changes
-- nothing about whether retrieval surfaces it, and the whole REINFORCE branch
-- is invisible to the only consumer that matters.
--
-- The fix is a separate timestamp rather than touching `valid_from`.
-- `valid_from` records when the fact became true, which is not the same as when
-- it was last mentioned: someone who has lived in Berlin since 2019 and says so
-- again today has not just moved there, and overwriting `valid_from` would
-- corrupt exactly the history "what did I say before X" reads.
--
-- Retrieval decays ACTIVE memories from coalesce(last_reinforced_at,
-- valid_from), so a restated fact is treated as fresh while its origin stays
-- accurate.

alter table memories add column if not exists last_reinforced_at timestamptz;

comment on column memories.last_reinforced_at is
  'When the user last restated this fact. Null if never reinforced. Recency '
  'decay reads coalesce(last_reinforced_at, valid_from); valid_from keeps '
  'meaning when the fact became true.';
