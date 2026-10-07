-- Run this once in Supabase → SQL Editor to clean up the LIVE tie-up
-- companies table (companies.json only affects future re-seeding, not
-- companies already in the database).

-- 1) Remove the duplicate "Quess Corp" entry, keep one copy.
delete from public.companies
where name in ('Quesscorp', 'Quess Corp')
  and id not in (
    select id from public.companies
    where name ilike '%quess%'
    order by sort_order asc
    limit 1
  );

-- 2) Remove the fake/placeholder entry (a person's name, not a company).
delete from public.companies where name = 'Prabhakaran';

-- 3) Review this one yourself before deleting — "DMS" is too generic to
--    safely auto-remove; uncomment if it's not a real tie-up company.
-- delete from public.companies where name = 'DMS';

-- Check what's left:
select id, name, sort_order from public.companies order by sort_order;
