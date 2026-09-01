-- Run this in Supabase Dashboard > SQL Editor.
-- It relies on Supabase's built-in auth.users table for actual login
-- (email/password hashing, sessions, etc). This just adds the extra
-- "who is this person" info on top.

create table if not exists public.profiles (
  id uuid references auth.users(id) on delete cascade primary key,
  role text not null check (role in ('student', 'company')),
  full_name text,
  college text,
  company_name text,
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- Each user can see and update only their own profile.
-- (The backend also uses the service-role key, which bypasses RLS
-- entirely, for admin actions like provisioning company accounts.)
create policy "Users can view own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "Users can update own profile"
  on public.profiles for update
  using (auth.uid() = id);

-- Optional but recommended: an index for quick role lookups.
create index if not exists profiles_role_idx on public.profiles (role);
