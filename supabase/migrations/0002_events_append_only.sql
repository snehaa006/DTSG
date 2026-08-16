-- Phase 3: make the event log's immutability a database guarantee.
--
-- Phase 1 described `events` as append-only, but nothing enforced it — the
-- service role could UPDATE or DELETE freely, so "immutable" was a convention
-- one buggy migration or one careless query away from being false. The whole
-- temporal model rests on raw messages never changing: if an event's text can
-- be rewritten, every memory derived from it becomes unverifiable and
-- "what did I say before X" stops being answerable.
--
-- These triggers reject UPDATE and DELETE at the row level, for every role
-- including service_role.
--
-- Escape hatch: real deletions do become necessary (a GDPR erasure request, a
-- test fixture teardown). Rather than leave the table mutable, we require the
-- caller to opt in explicitly within a transaction:
--
--   begin;
--   set local dtsg.allow_event_mutation = 'on';
--   delete from events where user_id = '...';
--   commit;
--
-- `set local` means the exemption dies with the transaction, so it cannot leak
-- into the pooled connection's next user. Ordinary application code never sets
-- it, which is the point: erasing history stays possible but never accidental.

create or replace function events_reject_mutation()
returns trigger
language plpgsql
as $$
begin
  if coalesce(current_setting('dtsg.allow_event_mutation', true), 'off') = 'on' then
    return coalesce(new, old);
  end if;

  raise exception
    'events is append-only: % rejected on event %', tg_op, coalesce(old.id, new.id)
    using hint = 'Set dtsg.allow_event_mutation to on inside a transaction to override.';
end;
$$;

drop trigger if exists events_no_update on events;
create trigger events_no_update
  before update on events
  for each row execute function events_reject_mutation();

drop trigger if exists events_no_delete on events;
create trigger events_no_delete
  before delete on events
  for each row execute function events_reject_mutation();
