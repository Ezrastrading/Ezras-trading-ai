-- ##############################################################################
-- rls_security_advisor_fix.sql
-- Supabase Security Advisor remediation:
-- - Fix "RLS Enabled No Policy" (default-deny must still have at least one non-trivial policy)
-- - Fix "RLS Policy Always True" (remove any USING(true)/WITH CHECK(true)-equivalent exposure)
--
-- Design stance (secure-by-default):
-- - These tables are internal/backoffice analytics + trading truth mirrors.
-- - Client roles (anon/authenticated) should have NO access by default.
-- - Server-side workflows must use the service role key.
--
-- Service role access model:
-- - RLS remains enabled, but we add policies that only pass when auth.role() = 'service_role'.
-- - This is not "always true" and keeps non-service JWTs blocked.
-- ##############################################################################

begin;

-- Ensure helper exists (Supabase standard), but do not require it for policy creation.
-- auth.role() is available in Supabase Postgres by default.

-- 1) Drop dangerously broad policies (USING true / WITH CHECK true) anywhere in public schema.
do $$
declare
  r record;
begin
  for r in
    select schemaname, tablename, policyname
    from pg_policies
    where schemaname = 'public'
      and (
        coalesce(qual, '') = 'true'
        or coalesce(with_check, '') = 'true'
      )
  loop
    execute format('drop policy if exists %I on %I.%I;', r.policyname, r.schemaname, r.tablename);
  end loop;
end$$;

-- 2) Lock down the known internal mirror tables (enable RLS + service-role-only policies).
--    Note: service role bypasses RLS at the Postgres role level in many Supabase setups, but we still
--    add explicit policies to keep Security Advisor clean and make intent auditable.
do $$
declare
  t text;
  tables text[] := array[
    'trade_events',
    'daily_trade_summary',
    'strategy_performance_summary',
    'avenue_performance_summary',
    'goal_progress_snapshots',
    'ceo_review_snapshots',
    'edge_registry'
  ];
begin
  foreach t in array tables loop
    if to_regclass('public.' || t) is null then
      continue;
    end if;

    -- Default deny for client roles.
    execute format('revoke all on table public.%I from anon;', t);
    execute format('revoke all on table public.%I from authenticated;', t);

    -- Ensure RLS is enabled.
    execute format('alter table public.%I enable row level security;', t);

    -- Drop any existing policies for these tables (we do not trust prior posture).
    -- This avoids lingering permissive policies that are not always-true but still too broad.
    for policyname in
      select policyname from pg_policies where schemaname='public' and tablename=t
    loop
      execute format('drop policy if exists %I on public.%I;', policyname, t);
    end loop;

    -- Recreate least-privilege policies: service_role only.
    execute format($sql$
      create policy %I on public.%I
      for select
      to public
      using (auth.role() = 'service_role');
    $sql$, t || '__svc_select', t);

    execute format($sql$
      create policy %I on public.%I
      for insert
      to public
      with check (auth.role() = 'service_role');
    $sql$, t || '__svc_insert', t);

    execute format($sql$
      create policy %I on public.%I
      for update
      to public
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
    $sql$, t || '__svc_update', t);

    execute format($sql$
      create policy %I on public.%I
      for delete
      to public
      using (auth.role() = 'service_role');
    $sql$, t || '__svc_delete', t);
  end loop;
end$$;

-- 3) Fix any remaining "RLS enabled, no policy" tables in public schema (default to backend-only).
--    This is safer than guessing client access. If a table should be user-scoped, add ownership
--    columns and replace with auth.uid()-scoped policies in a follow-up migration.
do $$
declare
  r record;
  p_name text;
begin
  for r in
    select c.relname as tablename
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'r'
      and c.relrowsecurity is true
      and not exists (
        select 1 from pg_policies p where p.schemaname='public' and p.tablename=c.relname
      )
  loop
    p_name := r.tablename || '__svc_select';
    execute format($sql$
      create policy %I on public.%I
      for select
      to public
      using (auth.role() = 'service_role');
    $sql$, p_name, r.tablename);
  end loop;
end$$;

commit;

