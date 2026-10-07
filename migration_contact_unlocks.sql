-- Run in Supabase SQL Editor. Records each time a company unlocks a student's contact.
create table if not exists public.contact_unlocks (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  student_id uuid not null references public.profiles(id) on delete cascade,
  created_at timestamptz not null default now(),
  unique (company_id, student_id)
);
alter table public.contact_unlocks enable row level security;
grant select, insert, update, delete on public.contact_unlocks to service_role;
