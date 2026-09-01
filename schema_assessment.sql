-- Run this AFTER schema.sql and schema_dashboard.sql, in Supabase Dashboard > SQL Editor.
-- Adds the technical-assessment engine: a status column on each student's
-- profile (used to gate access to the real student dashboard), and a log of
-- every attempt — round scores, pass/fail, and anti-cheat violation counts.

-- ---- Assessment status on the student profile ----
-- 'locked'   : hasn't attempted / passed any domain yet (default)
-- 'failed'   : most recent attempt didn't clear both rounds (or was flagged)
-- 'eligible' : passed both rounds for qualified_domain — profile is visible
--              to companies and the student can reach the dashboard
alter table public.profiles add column if not exists assessment_status text
  check (assessment_status in ('locked', 'failed', 'eligible')) default 'locked';
alter table public.profiles add column if not exists qualified_domain text;

-- ---- Attempt log (one row per finished attempt) ----
create table if not exists public.assessment_attempts (
  id uuid primary key default gen_random_uuid(),
  student_id uuid references auth.users(id) on delete cascade not null,
  domain text not null,
  round1_score int,            -- percentage, 0-100
  round1_passed boolean,
  round2_passed boolean,
  overall_passed boolean not null,
  violations int default 0,    -- anti-cheat strikes recorded during the attempt
  flagged boolean default false, -- true if auto-failed for hitting the violation limit
  created_at timestamptz not null default now()
);

alter table public.assessment_attempts enable row level security;

-- Students can read their own attempt history.
drop policy if exists "Students can view own attempts" on public.assessment_attempts;
create policy "Students can view own attempts"
  on public.assessment_attempts for select
  using (auth.uid() = student_id);

-- All writes go through the backend's service-role key (bypasses RLS),
-- so no insert/update policy is needed for the anon/browser role — the
-- frontend never writes to this table directly.

create index if not exists assessment_attempts_student_idx on public.assessment_attempts (student_id);
create index if not exists assessment_attempts_domain_idx on public.assessment_attempts (domain);
