-- Run in Supabase SQL Editor. Used by the admin dashboard to count profile views.
create table if not exists public.profile_views (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  student_id uuid not null references public.profiles(id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table public.profile_views enable row level security;
grant select, insert, update, delete on public.profile_views to service_role;
