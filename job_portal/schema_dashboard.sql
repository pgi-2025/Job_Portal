-- Run this AFTER schema.sql, in Supabase Dashboard > SQL Editor.
-- Adds the extra profile fields the dashboard needs, plus tables for
-- tie-up companies and upcoming drives (so the dashboard is fully
-- database-driven rather than hardcoded).

-- ---- Extra student profile fields ----
alter table public.profiles add column if not exists mobile_number text;
alter table public.profiles add column if not exists degree text;
alter table public.profiles add column if not exists age int;
alter table public.profiles add column if not exists resume_url text;
alter table public.profiles add column if not exists photo_url text;
alter table public.profiles add column if not exists updated_at timestamptz default now();

-- ---- Tie-up companies ----
create table if not exists public.companies (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  roles text[] default '{}',
  sort_order int default 0
);
alter table public.companies enable row level security;
drop policy if exists "Anyone can view companies" on public.companies;
create policy "Anyone can view companies"
  on public.companies for select
  using (true);

-- ---- Upcoming placement drives ----
create table if not exists public.drives (
  id uuid primary key default gen_random_uuid(),
  date_label text not null,
  title text not null,
  sort_order int default 0
);
alter table public.drives enable row level security;
drop policy if exists "Anyone can view drives" on public.drives;
create policy "Anyone can view drives"
  on public.drives for select
  using (true);

-- ---- Seed sample data (only if tables are empty) ----
do $$
begin
  if (select count(*) from public.companies) = 0 then
    insert into public.companies (name, roles, sort_order) values
      ('Vikram Solar Pvt Ltd', array['Trainee Engineer','QA Executive'], 1),
      ('Mahle Engine Components India Pvt Ltd', array['Production Engineer'], 2),
      ('Dae Seung Autoparts India Pvt Ltd', array['Graduate Engineer Trainee'], 3),
      ('Prabha Engineering Pvt Ltd', array['Design Engineer','Site Engineer'], 4),
      ('Wittur Elevator Components India Pvt. Ltd', array['Mechanical Engineer'], 5),
      ('Wipro Enterprises Private Limited', array['Process Engineer'], 6),
      ('Bonfiglioli Transmissions Pvt Ltd', array['Quality Engineer'], 7),
      ('Tata Electronics', array['Electronics Engineer','Production Associate'], 8),
      ('Foxconn', array['Assembly Engineer'], 9),
      ('Infac India Pvt Ltd', array['Trainee Engineer'], 10),
      ('Hangchang India Pvt Ltd', array['Quality Analyst'], 11),
      ('NIPPON STEEL', array['Process Associate'], 12),
      ('Abrains Technologies', array['Software Trainee'], 13),
      ('Synergyrevo Global Business Service Ltd', array['Business Associate'], 14),
      ('Ienergizer IT Services Pvt Ltd', array['Customer Support Associate'], 15),
      ('GJOBS India Pvt Ltd', array['Recruitment Associate'], 16);
  end if;

  if (select count(*) from public.drives) = 0 then
    insert into public.drives (date_label, title, sort_order) values
      ('Apr 16', 'NSIC Chennai', 1),
      ('TBA', 'To be announced', 2),
      ('TBA', 'To be announced', 3);
  end if;
end $$;
