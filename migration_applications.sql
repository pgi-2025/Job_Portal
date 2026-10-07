-- Run this in Supabase Dashboard > SQL Editor.

alter table public.companies add column if not exists application_fields text[] default '{}';

create table if not exists public.applications (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  student_id uuid not null references auth.users(id) on delete cascade,
  role text,
  answers jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique(company_id, student_id, role)
);

alter table public.applications enable row level security;
-- No public policy: accessed only via the backend's service-role key.
