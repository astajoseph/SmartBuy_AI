-- Row Level Security.
-- The backend connects with a privileged Postgres role and bypasses RLS; it filters user rows
-- itself using the user id from a verified Supabase JWT. RLS protects the tables if the
-- "smartbuy" schema is ever exposed through Supabase's REST/GraphQL APIs.
-- Every table gets RLS with no policy (deny-all through the API) except user-owned tables,
-- which allow access only to their owner. Policies are created only when Supabase's
-- auth schema exists, so this migration also runs on a plain PostgreSQL test database.

do $$
declare t text;
begin
    for t in select tablename from pg_tables where schemaname = 'smartbuy' and tablename <> 'schema_migrations' loop
        execute format('alter table smartbuy.%I enable row level security', t);
    end loop;

    if exists (select 1 from pg_namespace where nspname = 'auth')
       and exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                   where n.nspname = 'auth' and p.proname = 'uid') then
        execute 'create policy user_targets_owner on smartbuy.user_targets for all
                 using (auth.uid() = user_id) with check (auth.uid() = user_id)';
        execute 'create policy notifications_owner on smartbuy.notifications for select
                 using (user_id is null or auth.uid() = user_id)';
        execute 'create policy notifications_mark_read on smartbuy.notifications for update
                 using (auth.uid() = user_id) with check (auth.uid() = user_id)';
    end if;
end $$;
