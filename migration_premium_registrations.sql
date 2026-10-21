-- Run in Supabase SQL Editor.
create table if not exists public.premium_registrations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  phone text not null,
  email text not null,
  college text not null,
  domain text,
  message text,
  created_at timestamptz not null default now()
);
alter table public.premium_registrations enable row level security;
