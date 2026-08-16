-- Pin the append-only trigger function's search_path.
--
-- Supabase's linter flagged `events_reject_mutation` as having a role-mutable
-- search_path. For a function that exists to *refuse* writes, that matters more
-- than usual: resolution of anything it references would depend on the caller's
-- search_path, which the caller controls. Setting it empty means every
-- identifier resolves explicitly or not at all.
--
-- 0002 is left as applied rather than edited — it ran against the live database
-- already, and rewriting applied migrations makes the file history stop
-- describing what actually happened.

create or replace function events_reject_mutation()
returns trigger
language plpgsql
set search_path = ''
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
