-- Run this in Supabase Dashboard > SQL Editor.
-- Hall of Fame: a company "selects" a candidate (visible to all companies
-- as a green highlight), and only once the student also confirms does the
-- student get placed (added to the Hall of Fame + blocked from applying
-- to any other company).

alter table public.profiles add column if not exists placed boolean not null default false;
alter table public.profiles add column if not exists placed_company_id uuid references public.companies(id);
alter table public.profiles add column if not exists placed_company_name text;

create table if not exists public.company_selections (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  student_id uuid not null references auth.users(id) on delete cascade,
  student_confirmed boolean not null default false,
  created_at timestamptz not null default now(),
  unique(company_id, student_id)
);
alter table public.company_selections enable row level security;
-- No public policy: accessed only via the backend's service-role key.
