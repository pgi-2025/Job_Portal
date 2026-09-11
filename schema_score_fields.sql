-- Run this in Supabase Dashboard > SQL Editor (after schema_assessment.sql).
-- Adds real "correct answers / total questions" score fields, used to show
-- a student's actual test score (instead of only a percentage) on their
-- profile and on the company candidate view.

alter table public.assessment_attempts add column if not exists round1_correct int;
alter table public.assessment_attempts add column if not exists round1_total int;

alter table public.profiles add column if not exists round1_correct int;
alter table public.profiles add column if not exists round1_total int;
alter table public.profiles add column if not exists last_login timestamptz;
