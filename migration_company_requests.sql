-- Run this in Supabase Dashboard > SQL Editor.
-- Stores "request access" submissions from the company login page
-- (companies.json/companies table is only for already-provisioned tie-ups).

create table if not exists public.company_access_requests (
  id uuid primary key default gen_random_uuid(),
  company_name text not null,
  email text not null,
  website text not null,
  contact_number text not null,
  location text not null,
  status text not null default 'pending',
  created_at timestamptz not null default now()
);

alter table public.company_access_requests enable row level security;
-- No public select/insert policy: all access goes through the backend's
-- service-role key (supabase_admin), which bypasses RLS.
