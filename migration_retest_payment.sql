-- Run in Supabase SQL Editor. First test is free; every later test costs Rs 9.
alter table public.profiles add column if not exists free_test_used boolean default false;
-- Students who already attempted before this feature count as having used their free test.
update public.profiles set free_test_used = true
  where id in (select distinct student_id from public.assessment_attempts);

create table if not exists public.assessment_payments (
  id uuid primary key default gen_random_uuid(),
  student_id uuid references auth.users(id) on delete cascade not null,
  razorpay_order_id text unique not null,
  razorpay_payment_id text,
  amount_paise int not null default 900,
  status text not null default 'created' check (status in ('created','paid','consumed')),
  created_at timestamptz not null default now()
);
alter table public.assessment_payments enable row level security;
create index if not exists assessment_payments_student_idx on public.assessment_payments (student_id);
